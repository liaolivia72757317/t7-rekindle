package org.t7rekindle.server.service;

import java.util.HashSet;
import org.junit.jupiter.api.Test;
import org.springframework.security.crypto.argon2.Argon2PasswordEncoder;
import org.t7rekindle.server.domain.Problem;
import static org.assertj.core.api.Assertions.*;

class InputsAndSecretsTest {
    @Test void validatesWithoutNormalizingInput() {
        Inputs.loginName("sample-user_1.0");
        for (String name : new String[]{null, "ab", "Aaa", " foo", "a".repeat(65), "用户一"}) {
            assertThatThrownBy(() -> Inputs.loginName(name)).isInstanceOf(Problem.class);
        }
        for (String password : new String[]{null, "short", "a".repeat(129)}) {
            assertThatThrownBy(() -> Inputs.password(password)).isInstanceOf(Problem.class);
        }
        Inputs.password("  spaces  ");
        Inputs.password("a".repeat(128));
        assertThat(Inputs.prefix("a_b")).isEqualTo("a\\_b");
        assertThat(Inputs.prefix("")).isEmpty();
        assertThatThrownBy(() -> Inputs.prefix(null)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> Inputs.prefix("%bad")).isInstanceOf(Problem.class);
        assertThat(Inputs.offset(100000)).isEqualTo(5000000);
        assertThatThrownBy(() -> Inputs.offset(-1)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> Inputs.offset(100001)).isInstanceOf(Problem.class);
    }
    @Test void secretsHaveEntropyAndHashesNeverRequirePlaintextStorage() {
        var secrets = new Secrets(new Argon2PasswordEncoder(16, 32, 1, 19456, 2));
        var tokens = new HashSet<String>();
        for (int i = 0; i < 100; i++) {
            String token = secrets.randomToken();
            assertThat(token).matches("[A-Za-z0-9_-]{43}");
            tokens.add(token);
        }
        assertThat(tokens).hasSize(100);
        String hash = secrets.encode(" PASSWORD ");
        assertThat(hash).startsWith("$argon2id$");
        assertThat(secrets.matches(" PASSWORD ", hash)).isTrue();
        assertThat(secrets.matches("PASSWORD", hash)).isFalse();
        assertThat(secrets.matches("PASSWORD", null)).isFalse();
        assertThat(Secrets.hash("abc")).isEqualTo("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }
}
