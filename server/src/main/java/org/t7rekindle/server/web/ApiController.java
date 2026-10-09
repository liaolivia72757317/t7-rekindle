package org.t7rekindle.server.web;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.t7rekindle.server.domain.Models.*;
import org.t7rekindle.server.service.AuthService;
import org.t7rekindle.server.service.PresenceService;

@RestController
@RequestMapping("/api/v1/auth")
public class ApiController {
    public record Login(String loginName, String password) { }
    private final AuthService auth;
    private final PresenceService presence;

    public ApiController(AuthService auth, PresenceService presence) { this.auth = auth; this.presence = presence; }

    @PostMapping("/sessions")
    @ResponseStatus(HttpStatus.CREATED)
    public SessionCredentials login(@RequestBody Login login, HttpServletRequest request) {
        return auth.login(login.loginName(), login.password(), request.getRemoteAddr());
    }
    @GetMapping("/session")
    public Identity current(@AuthenticationPrincipal Identity identity) { return identity; }
    @DeleteMapping("/session")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void logout(@AuthenticationPrincipal Identity identity) { auth.logout(identity); }
    @PostMapping("/online")
    public OnlineConnection online(@AuthenticationPrincipal Identity identity) { return presence.online(identity); }
    @PostMapping("/online/{connectionId}/heartbeat")
    public OnlineConnection heartbeat(@AuthenticationPrincipal Identity identity, @PathVariable String connectionId) {
        return presence.heartbeat(identity, connectionId);
    }
    @DeleteMapping("/online/{connectionId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void offline(@AuthenticationPrincipal Identity identity, @PathVariable String connectionId) {
        presence.offline(identity, connectionId);
    }
}
