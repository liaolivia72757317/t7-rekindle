package org.t7rekindle.server.web;

import java.time.Clock;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpSession;
import org.t7rekindle.server.service.AccountService.GeneratedPassword;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class SecretResultsTest {
    @Test void passwordsAreScopedConsumedExpiringAndBounded() {
        Clock clock = mock(Clock.class);
        when(clock.instant()).thenReturn(Instant.EPOCH);
        var results = new SecretResults(clock);
        var session = new MockHttpSession();
        var password = new GeneratedPassword("sample", "GENERATED_PASSWORD");
        String key = results.store(session, password);
        assertThat(results.consume(new MockHttpSession(), key)).isNull();
        assertThat(results.consume(session, key)).isEqualTo(password);
        assertThat(results.consume(session, key)).isNull();
        key = results.store(session, password);
        when(clock.instant()).thenReturn(Instant.EPOCH.plusSeconds(300));
        assertThat(results.consume(session, key)).isNull();
        when(clock.instant()).thenReturn(Instant.EPOCH);
        String first = results.store(session, password);
        for (int i = 0; i < 10; i++) results.store(session, password);
        assertThat(results.consume(session, first)).isNull();
        when(clock.instant()).thenReturn(Instant.EPOCH.plusSeconds(300));
        assertThat(results.store(session, password)).isNotNull();
    }
}
