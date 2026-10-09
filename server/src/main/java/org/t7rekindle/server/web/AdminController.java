package org.t7rekindle.server.web;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.*;
import org.t7rekindle.server.config.SecurityConfig;
import org.t7rekindle.server.domain.Models.AdminIdentity;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.service.AccountService;
import org.t7rekindle.server.service.AdminService;

@Controller
@RequestMapping("/admin")
public class AdminController {
    private final AdminService admins;
    private final AccountService accounts;
    private final SecretResults results;

    public AdminController(AdminService admins, AccountService accounts, SecretResults results) {
        this.admins = admins;
        this.accounts = accounts;
        this.results = results;
    }
    @GetMapping("/login") String login() { return "login"; }
    @PostMapping("/login")
    String login(@RequestParam String loginName, @RequestParam String password, HttpServletRequest request) {
        AdminIdentity identity = admins.login(loginName, password, request.getRemoteAddr());
        HttpSession previous = request.getSession(false);
        if (previous != null) previous.invalidate();
        request.getSession(true).setAttribute(SecurityConfig.ADMIN_IDENTITY, identity);
        return identity.mustChangePassword() ? "redirect:/admin/password" : "redirect:/admin/accounts";
    }
    @PostMapping("/logout")
    String logout(HttpServletRequest request) {
        request.getSession().invalidate();
        return "redirect:/admin/login";
    }
    @GetMapping({"", "/"}) String home() { return "redirect:/admin/accounts"; }
    @GetMapping("/password") String password(@AuthenticationPrincipal AdminIdentity admin, Model model) {
        model.addAttribute("mustChange", admin.mustChangePassword());
        return "password";
    }
    @PostMapping("/password")
    String password(@AuthenticationPrincipal AdminIdentity admin, @RequestParam String oldPassword,
            @RequestParam String newPassword, @RequestParam String confirmation, HttpServletRequest request) {
        if (!newPassword.equals(confirmation)) throw new Problem(400, "PASSWORD_MISMATCH", "两次新密码不一致");
        admins.changePassword(admin, oldPassword, newPassword);
        request.getSession().invalidate();
        return "redirect:/admin/login?changed";
    }
    @GetMapping("/accounts")
    String accounts(@RequestParam(defaultValue = "") String query, @RequestParam(defaultValue = "0") int page, Model model) {
        model.addAttribute("accounts", accounts.list(query, page));
        model.addAttribute("query", query);
        model.addAttribute("page", page);
        return "accounts";
    }
    @PostMapping("/accounts")
    String create(@AuthenticationPrincipal AdminIdentity admin, @RequestParam String loginName, HttpSession session) {
        return "redirect:/admin/results/" + results.store(session, accounts.create(admin, loginName));
    }
    @GetMapping("/accounts/{id}")
    String account(@PathVariable String id, @RequestParam(defaultValue = "0") int page, Model model) {
        model.addAttribute("account", accounts.account(id));
        model.addAttribute("sessions", accounts.sessions(id, page));
        model.addAttribute("page", page);
        return "account";
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
    String audit(@RequestParam(defaultValue = "0") int page, Model model) {
        model.addAttribute("records", accounts.audits(page));
        model.addAttribute("page", page);
        return "audit";
    }
}
