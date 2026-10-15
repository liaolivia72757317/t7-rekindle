package org.interestslab.t7rekindle.server.web;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.interestslab.t7rekindle.server.domain.Models.QqSessionCredentials;
import org.interestslab.t7rekindle.server.service.QqAuthService;

@RestController
@RequestMapping("/api/v1/auth/qq")
public class QqAuthController {
    public record Login(String machineId, String qqSessionCookie, String gameName) { }
    private final QqAuthService auth;
    public QqAuthController(QqAuthService auth) { this.auth = auth; }

    @PostMapping("/sessions")
    @ResponseStatus(HttpStatus.CREATED)
    public QqSessionCredentials login(@RequestBody Login login, HttpServletRequest request) {
        return auth.login(login.machineId(), login.qqSessionCookie(), request.getRemoteAddr(), login.gameName());
    }
}
