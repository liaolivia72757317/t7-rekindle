package org.interestslab.t7rekindle.server.service;

import org.junit.jupiter.api.Test;
import org.interestslab.t7rekindle.server.domain.Problem;
import static org.assertj.core.api.Assertions.*;

class GameNameRulesTest {
    @Test void preservesCaseAndTrimsUnicodeWhitespace() {
        assertThat(GameNameRules.normalize("\u3000 Player \u0085")).isEqualTo("Player");
        assertThat(GameNameRules.normalize("新玩家")).isEqualTo("新玩家");
        assertThat(GameNameRules.normalize("玩家！")).isEqualTo("玩家！");
        assertThat(GameNameRules.normalize("a".repeat(31))).hasSize(31);
        assertThat(GameNameRules.normalize("名".repeat(15) + "A")).endsWith("A");
    }
    @Test void rejectsInvalidNames() {
        for (String name : new String[] {null, "", " \t", "\u0000", "a\nb", "a\u007fb", "a\u0085b",
                "😀", "€", "\ue000", "\uf8ff", "a".repeat(32), "名".repeat(16)}) {
            assertThatThrownBy(() -> GameNameRules.normalize(name)).isInstanceOfSatisfying(Problem.class,
                    error -> assertThat(error.code()).isEqualTo("INVALID_GAME_NAME"));
        }
    }
    @Test void requiresCompletedRegistration() {
        assertThatThrownBy(() -> GameNameRules.requireRegistered(null)).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("REGISTRATION_REQUIRED"));
        assertThatThrownBy(() -> GameNameRules.requireRegistered("")).isInstanceOf(Problem.class);
        GameNameRules.requireRegistered("玩家");
    }
}
