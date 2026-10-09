package org.t7rekindle.server.service;

import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.t7rekindle.server.domain.Models.*;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.persistence.AdminMapper;
import org.t7rekindle.server.persistence.AuthMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AuthServiceTest {
    private final AuthMapper db = mock(AuthMapper.class);
    private final AdminMapper audit = mock(AdminMapper.class);
    private final Secrets secrets = mock(Secrets.class);
    private final LoginLimiter limiter = mock(LoginLimiter.class);
    private final Instant now = Instant.parse("2026-01-01T00:00:00Z");
    private final Account account = new Account("account", "sample", "hash", "ACTIVE", 0, now);
    private final Identity identity = new Identity("account", "session", "sample", now.plusSeconds(100));
    private final String token = "A".repeat(43);
    private AuthService service;

    @BeforeEach void setup() {
        service = new AuthService(db, audit, secrets, limiter, TestTransactions.immediate());
        when(db.now()).thenReturn(now);
        when(db.account("account")).thenReturn(account);
        when(db.lockAccount("account")).thenReturn(account);
        when(db.lockByName("sample")).thenReturn(account);
        when(db.sessionByHash(anyString())).thenReturn(session(0, now.plusSeconds(100), null, "account"));
        when(db.lockSession("session")).thenReturn(session(0, now.plusSeconds(100), null, "account"));
        when(secrets.randomToken()).thenReturn(token);
        when(secrets.matches("PASSWORD", "hash")).thenReturn(true);
    }
    @Test void loginCreatesAbsoluteSessionWithoutPresence() {
        assertThat(service.login("sample", "PASSWORD", "IP").expiresAt()).isEqualTo(now.plusSeconds(30L * 86400));
        verify(db).createSession(argThat(row -> row.authEpoch() == 0 && row.tokenHash().equals(Secrets.hash(token))));
        verify(db, never()).savePresence(any());
        verify(limiter).check("account", "sample", "IP");
    }
    @Test void loginHasUniformFailuresAndRecordsThem() {
        when(db.lockByName("sample")).thenReturn(null);
        failsLogin();
        when(db.lockByName("sample")).thenReturn(account);
        when(secrets.matches("PASSWORD", "hash")).thenReturn(false);
        failsLogin();
        when(secrets.matches("PASSWORD", "hash")).thenReturn(true);
        when(db.lockByName("sample")).thenReturn(new Account("account", "sample", "hash", "DISABLED", 0, now));
        failsLogin();
        verify(audit, times(3)).audit("anonymous", "ACCOUNT_LOGIN_FAILED", "sample");
    }
    @Test void authenticationRejectsEveryInvalidCredentialState() {
        assertThatThrownBy(() -> service.authenticate(null)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.authenticate("short")).isInstanceOf(Problem.class);
        when(db.sessionByHash(anyString())).thenReturn(null);
        failsAuthentication();
        when(db.sessionByHash(anyString())).thenReturn(session(0, now.plusSeconds(100), null, "account"));
        when(db.account("account")).thenReturn(null);
        failsAuthentication();
        when(db.account("account")).thenReturn(new Account("other", "sample", "hash", "ACTIVE", 0, now));
        failsAuthentication();
        when(db.account("account")).thenReturn(new Account("account", "sample", "hash", "DISABLED", 0, now));
        failsAuthentication();
        when(db.account("account")).thenReturn(account);
        when(db.sessionByHash(anyString())).thenReturn(session(0, now.plusSeconds(100), now, "account"));
        failsAuthentication();
        when(db.sessionByHash(anyString())).thenReturn(session(1, now.plusSeconds(100), null, "account"));
        failsAuthentication();
        when(db.sessionByHash(anyString())).thenReturn(session(0, now, null, "account"));
        failsAuthentication();
        when(db.sessionByHash(anyString())).thenReturn(session(0, now.plusSeconds(100), null, "account"));
        assertThat(service.authenticate(token)).isEqualTo(identity);
    }
    @Test void lockingRevalidatesAndLogoutOnlyReleasesItsOwnPresence() {
        service.logout(identity);
        var order = inOrder(db);
        order.verify(db).lockAccount("account");
        order.verify(db).lockSession("session");
        order.verify(db).now();
        order.verify(db).revokeSession("session", "LOGOUT");
        order.verify(db).clearSessionPresence("account", "session");
        when(db.lockSession("session")).thenReturn(null);
        assertThatThrownBy(() -> service.requireLocked(identity)).isInstanceOf(Problem.class);
    }
    private void failsLogin() { assertThatThrownBy(() -> service.login("sample", "PASSWORD", "IP")).isInstanceOf(Problem.class); }
    private void failsAuthentication() { assertThatThrownBy(() -> service.authenticate(token)).isInstanceOf(Problem.class); }
    private Session session(long epoch, Instant expires, Instant revoked, String owner) {
        return new Session("session", owner, Secrets.hash(token), epoch, now, expires, revoked, null);
    }
}
