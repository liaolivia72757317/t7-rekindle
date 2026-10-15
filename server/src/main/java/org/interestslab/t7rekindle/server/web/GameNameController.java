package org.interestslab.t7rekindle.server.web;

import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.service.GameNameService;

@RestController
@RequestMapping("/api/v1/auth/game-name")
public class GameNameController {
    private final GameNameService names;
    public GameNameController(GameNameService names) { this.names = names; }

    @GetMapping
    public GameName current(@AuthenticationPrincipal Identity identity) { return new GameName(identity.gameName()); }

    @PutMapping
    public GameName initialize(@AuthenticationPrincipal Identity identity, @RequestBody GameName body) {
        return names.initialize(identity, body.gameName());
    }
}
