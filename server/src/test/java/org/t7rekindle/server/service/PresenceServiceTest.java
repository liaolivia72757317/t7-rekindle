package org.t7rekindle.server.service;

import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;
import org.t7rekindle.server.domain.Models.*;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.persistence.AuthMapper;
import org.assertj.core.api.ThrowableAssert.ThrowingCallable;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class PresenceServiceTest {
    private final AuthMapper db = mock(AuthMapper.class);
    private final AuthService auth = mock(AuthService.class);
    private final TransactionTemplate tx = mock(TransactionTemplate.class);
    private final Instant now = Instant.parse("2026-01-01T00:00:00Z");
    private final Identity identity = new Identity("account", "session", "sample", now.plusSeconds(100));
    private PresenceService service;

    @BeforeEach void setup() {
        when(tx.execute(any())).thenAnswer(call -> {
            TransactionCallback<?> callback = call.getArgument(0);
            return callback.doInTransaction(new SimpleTransactionStatus());
        });
        doCallRealMethod().when(tx).executeWithoutResult(any());
        when(db.now()).thenReturn(now);
        when(auth.requireLocked(identity)).thenReturn(identity);
        service = new PresenceService(db, auth, tx);
    }

    @Test void explicitOnlineCreatesNewConnectionAndClampsDeadline() {
        var first = service.online(identity);
        var second = service.online(identity);
        assertThat(first.connectionId()).isNotEqualTo(second.connectionId());
        assertThat(first.leaseExpiresAt()).isEqualTo(now.plusSeconds(45));
        assertThat(first.heartbeatIntervalSeconds()).isEqualTo(15);
        var expiring = new Identity("account", "session", "sample", now.plusSeconds(5));
        when(auth.requireLocked(expiring)).thenReturn(expiring);
        assertThat(service.online(expiring).leaseExpiresAt()).isEqualTo(expiring.expiresAt());
    }

    @Test void heartbeatRequiresLiveMatchingOwner() {
        assertCode(() -> service.heartbeat(identity, "old"), "NOT_ONLINE");
        when(db.lockPresence("account")).thenReturn(new Presence("account", "session", "old", now));
        assertCode(() -> service.heartbeat(identity, "old"), "NOT_ONLINE");
        when(db.lockPresence("account")).thenReturn(new Presence("account", "other", "old", now.plusSeconds(5)));
        assertCode(() -> service.heartbeat(identity, "old"), "ONLINE_TAKEN_OVER");
        when(db.lockPresence("account")).thenReturn(new Presence("account", "session", "new", now.plusSeconds(5)));
        assertCode(() -> service.heartbeat(identity, "old"), "ONLINE_TAKEN_OVER");
        assertThat(service.heartbeat(identity, "new").leaseExpiresAt()).isEqualTo(now.plusSeconds(45));
        verify(db, times(1)).savePresence(any());
    }

    @Test void offlineOnlyDeletesExactConnection() {
        service.offline(identity, "old");
        verify(db).clearConnection("account", "session", "old");
        verify(db, never()).revokeSession(any(), any());
    }

    static void assertCode(ThrowingCallable call, String code) {
        assertThatThrownBy(call).isInstanceOfSatisfying(Problem.class, error -> {
            assertThat(error.status()).isEqualTo(409);
            assertThat(error.code()).isEqualTo(code);
        });
    }
}
