package org.interestslab.t7rekindle.server.web;

import jakarta.servlet.http.HttpServletResponse;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.*;
import org.interestslab.t7rekindle.server.domain.Models.AdminIdentity;
import org.interestslab.t7rekindle.server.domain.TitleModels.Draft;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.service.TitleService;
import org.interestslab.t7rekindle.server.service.AccountService;
import org.interestslab.t7rekindle.server.service.Inputs;
import static org.interestslab.t7rekindle.server.domain.TitleModels.*;

@Controller
public class TitleAdminController {
    private final TitleService titles;
    private final AccountService accounts;
    public TitleAdminController(TitleService titles, AccountService accounts) { this.titles = titles; this.accounts = accounts; }
    @GetMapping("/admin/titles")
    String catalogue(@RequestParam(required = false) Integer edit, Model model) {
        var definitions = titles.definitions();
        var selected = edit == null ? null : definitions.stream().filter(t -> t.titleId() == edit)
                .findFirst().orElseThrow(Problem::missing);
        model.addAttribute("titles", definitions);
        model.addAttribute("draftTitleId", edit);
        model.addAttribute("original", selected != null && !selected.custom());
        if (!model.containsAttribute("draft")) model.addAttribute("draft", selected == null
                ? new Draft("", "", 0, true)
                : new Draft(selected.name(), selected.description(), selected.sortOrder(), selected.enabled()));
        if (selected != null && !selected.custom()) {
            var draft = (Draft) model.getAttribute("draft");
            model.addAttribute("draft", new Draft(selected.name(), draft.description(), draft.sortOrder(), draft.enabled()));
        }
        return "titles";
    }
    @PostMapping("/admin/titles")
    String create(@AuthenticationPrincipal AdminIdentity admin, @RequestParam String name,
                  @RequestParam String description, @RequestParam int sortOrder,
                  @RequestParam(defaultValue = "false") boolean enabled, Model model, HttpServletResponse response) {
        var draft = new Draft(name, description, sortOrder, enabled);
        try { titles.create(admin, draft); return "redirect:/admin/titles?created"; }
        catch (Problem error) {
            if (error.status() != 400) throw error;
            response.setStatus(400);
            model.addAttribute("error", error.getMessage()); model.addAttribute("draft", draft);
            return catalogue(null, model);
        }
    }
    @PostMapping("/admin/titles/{id}")
    String configure(@AuthenticationPrincipal AdminIdentity admin, @PathVariable int id,
                     @RequestParam(required = false) String name,
                     @RequestParam String description, @RequestParam int sortOrder,
                     @RequestParam(defaultValue = "false") boolean enabled, Model model, HttpServletResponse response) {
        var draft = new Draft(name, description, sortOrder, enabled);
        try { titles.configure(admin, id, draft); return "redirect:/admin/titles?saved"; }
        catch (Problem error) {
            if (error.status() != 400) throw error;
            response.setStatus(400);
            model.addAttribute("error", error.getMessage());
            model.addAttribute("draft", draft);
            return catalogue(id, model);
        }
    }
    @GetMapping("/admin/titles/{id}/grants")
    String recipients(@PathVariable int id, @RequestParam(defaultValue = "") String query,
                      @RequestParam(defaultValue = "0") int page, Model model) {
        model.addAttribute("title", titles.definition(id));
        model.addAttribute("recipients", titles.recipients(id, query, page));
        model.addAttribute("query", query); model.addAttribute("page", page);
        return "title-grants";
    }
    @PostMapping("/admin/titles/{id}/grants/{account}/grant")
    String grantTitle(@AuthenticationPrincipal AdminIdentity admin, @PathVariable int id, @PathVariable String account,
                      @RequestParam(defaultValue = "") String query, @RequestParam(defaultValue = "0") int page) {
        Inputs.prefix(query); Inputs.offset(page);
        titles.grant(admin, account, id);
        return grantRedirect(id, query, page);
    }
    @PostMapping("/admin/titles/{id}/grants/{account}/revoke")
    String revokeTitle(@AuthenticationPrincipal AdminIdentity admin, @PathVariable int id, @PathVariable String account,
                       @RequestParam(defaultValue = "") String query, @RequestParam(defaultValue = "0") int page) {
        Inputs.prefix(query); Inputs.offset(page);
        titles.revoke(admin, account, id);
        return grantRedirect(id, query, page);
    }
    private static String grantRedirect(int id, String query, int page) {
        return "redirect:/admin/titles/" + id + "/grants?saved&query="
                + URLEncoder.encode(query, StandardCharsets.UTF_8) + "&page=" + page;
    }
    @GetMapping("/admin/accounts/{account}/titles")
    String account(@PathVariable String account, Model model) {
        model.addAttribute("account", accounts.account(account));
        model.addAttribute("snapshot", titles.account(account));
        return "account-titles";
    }
    @PostMapping("/admin/accounts/{account}/titles/{id}/grant")
    String grant(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String account, @PathVariable int id) {
        titles.grant(admin, account, id); return "redirect:/admin/accounts/" + account + "/titles";
    }
    @PostMapping("/admin/accounts/{account}/titles/{id}/revoke")
    String revoke(@AuthenticationPrincipal AdminIdentity admin, @PathVariable String account, @PathVariable int id) {
        titles.revoke(admin, account, id); return "redirect:/admin/accounts/" + account + "/titles";
    }
}
