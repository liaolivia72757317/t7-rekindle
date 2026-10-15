package org.interestslab.t7rekindle.server.web;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpSession;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.*;
import org.interestslab.t7rekindle.server.config.SecurityConfig;
import org.interestslab.t7rekindle.server.domain.Models.AdminIdentity;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.service.AccountService;
import org.interestslab.t7rekindle.server.service.GameNameService;
import org.interestslab.t7rekindle.server.service.AnnouncementService;
import org.interestslab.t7rekindle.server.service.AdminService;
import org.interestslab.t7rekindle.server.service.QqGroupAdminService;

@Controller
@RequestMapping("/admin")
public class AdminController {
    private final AdminService admins;
    private final AccountService accounts;
    private final GameNameService names;
    private final SecretResults results;
    private final QqGroupAdminService groups;
    private final AnnouncementService announcements;

    public AdminController(AdminService admins, AccountService accounts, SecretResults results, QqGroupAdminService groups,
            AnnouncementService announcements, GameNameService names) {
        this.admins = admins;
        this.accounts = accounts;
        this.names = names;
        this.results = results;
        this.groups = groups;
        this.announcements = announcements;
    }
    @GetMapping("/login") String login() { return "login"; }
    @GetMapping("/ui-components") String components() { return "ui-components"; }
    @PostMapping("/login")
    String login(@RequestParam String loginName, @RequestParam String password, HttpServletRequest request,
            HttpServletResponse response, Model model) {
        AdminIdentity identity;
        try { identity = admins.login(loginName, password, request.getRemoteAddr()); }
        catch (Problem error) {
            if (error.status() != 400 && error.status() != 401) throw error;
            response.setStatus(error.status());
            response.setHeader("Cache-Control", "no-store");
            model.addAttribute("error", error.getMessage());
            return "login";
        }
        HttpSession previous = request.getSession(false);
        if (previous != null) previous.invalidate();
        request.getSession(true).setAttribute(SecurityConfig.ADMIN_IDENTITY, identity);
        return identity.mustChangePassword() ? "redirect:/admin/password" : "redirect:/admin";
    }
    @PostMapping("/logout")
    String logout(HttpServletRequest request) {
        request.getSession().invalidate();
        return "redirect:/admin/login";
    }
    @GetMapping({"", "/"}) String home(Model model) {
        model.addAttribute("stats", accounts.stats());
        model.addAttribute("announcement", announcements.latest());
        model.addAttribute("settings", groups.view());
        model.addAttribute("records", accounts.audits(0).stream().limit(5).toList());
        return "home";
    }
    @GetMapping("/password") String password(@AuthenticationPrincipal AdminIdentity admin, Model model) {
        model.addAttribute("mustChange", admin.mustChangePassword());
        return "password";
    }
    @PostMapping("/password")
    String password(@AuthenticationPrincipal AdminIdentity admin, @RequestParam String oldPassword,
            @RequestParam String newPassword, @RequestParam String confirmation, HttpServletRequest request,
            HttpServletResponse response, Model model) {
        try {
            if (!newPassword.equals(confirmation)) throw new Problem(400, "PASSWORD_MISMATCH", "两次新密码不一致");
            admins.changePassword(admin, oldPassword, newPassword);
        } catch (Problem error) {
            response.setStatus(error.status());
            model.addAttribute("error", error.getMessage());
            return password(admin, model);
        }
        request.getSession().invalidate();
        return "redirect:/admin/login?changed";
    }
    @GetMapping("/accounts")
    String accounts(@RequestParam(defaultValue = "") String query, @RequestParam(defaultValue = "0") int page, Model model) {
        model.addAttribute("accounts", accounts.list(query, page));
        model.addAttribute("groupChecksEnabled", groups.view().enabled());
        model.addAttribute("query", query);
        model.addAttribute("page", page);
        return "accounts";
    }
    @PostMapping("/accounts")
    String create(@AuthenticationPrincipal AdminIdentity admin, @RequestParam String loginName, @RequestParam String gameName, HttpSession session,
            HttpServletResponse response, Model model) {
        try { return "redirect:/admin/results/" + results.store(session, accounts.create(admin, loginName, gameName)); }
        catch (Problem error) {
            if (error.status() != 400 && error.status() != 409) throw error;
            response.setStatus(error.status());
            model.addAttribute("error", error.getMessage());
            model.addAttribute("createLoginName", loginName);
            model.addAttribute("createGameName", gameName);
            return accounts("", 0, model);
        }
    }
    @GetMapping("/accounts/{id}")
    String account(@PathVariable String id, @RequestParam(defaultValue = "0") int page, Model model) {
        model.addAttribute("account", accounts.account(id));
        model.addAttribute("sessions", accounts.sessions(id, page));
        model.addAttribute("page", page);
        return "account";
    }
    @PostMapping("/accounts/{id}/game-name")
    String gameName(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String id,
                   @RequestParam String gameName, HttpServletResponse response, Model model) {
        try { names.rename(admin, id, gameName); }
        catch (Problem error) {
            if (error.status() != 400 && error.status() != 409) throw error;
            response.setStatus(error.status());
            model.addAttribute("error", error.getMessage());
            model.addAttribute("editGameName", gameName);
            return account(id, 0, model);
        }
        return "redirect:/admin/accounts/" + id;
    }
    @PostMapping("/accounts/{id}/status")
    String status(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String id, @RequestParam boolean enabled) {
        accounts.setEnabled(admin, id, enabled);
        return "redirect:/admin/accounts/" + id;
    }
    @PostMapping("/accounts/{id}/password")
    String reset(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String id, HttpSession session) {
        return "redirect:/admin/results/" + results.store(session, accounts.resetPassword(admin, id));
    }
    @PostMapping("/accounts/{id}/sessions/revoke-all")
    String revokeAll(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String id) {
        accounts.revokeAll(admin, id);
        return "redirect:/admin/accounts/" + id;
    }
    @PostMapping("/accounts/{id}/sessions/{sessionId}/revoke")
    String revoke(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String id, @PathVariable String sessionId) {
        accounts.revoke(admin, id, sessionId);
        return "redirect:/admin/accounts/" + id;
    }
    @GetMapping("/results/{id}")
    String result(@PathVariable String id, HttpSession session, Model model) {
        model.addAttribute("result", results.consume(session, id));
        return "result";
    }
    @GetMapping("/audit")
    String audit(@RequestParam(defaultValue = "all") String scope,
            @RequestParam(defaultValue = "0") int page, Model model) {
        if (!java.util.List.of("all", "player", "system").contains(scope)) throw Problem.invalid();
        model.addAttribute("auditSection", "all".equals(scope) ? "audit" : "audit-" + scope);
        model.addAttribute("auditGroup", "player".equals(scope) ? "玩家管理" : ("system".equals(scope) ? "系统管理" : null));
        model.addAttribute("auditTitle", "player".equals(scope) ? "玩家操作记录" : ("system".equals(scope) ? "系统操作记录" : "操作记录"));
        if (!"all".equals(scope)) return "construction";
        model.addAttribute("records", accounts.audits(page));
        model.addAttribute("page", page);
        return "audit";
    }
}
