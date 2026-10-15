package org.interestslab.t7rekindle.server.service;

import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DuplicateKeyException;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.AuthMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AccountServiceTest {
    private final AuthMapper db = mock(AuthMapper.class);
    private final AdminMapper audit = mock(AdminMapper.class);
    private final AdminService admins = mock(AdminService.class);
    private final Secrets secrets = mock(Secrets.class);
    private final AdminIdentity admin = new AdminIdentity(0, false);
    private final Account account = new Account("id", "sample", "hash", "ACTIVE", 0, Instant.EPOCH);
    private AccountService service;

    @BeforeEach void setup() {
        service = new AccountService(db, audit, admins, secrets, TestTransactions.immediate(), mock(SessionEvents.class));
        when(db.account("id")).thenReturn(account);
        when(db.lockAccount("id")).thenReturn(account);
        when(secrets.randomToken()).thenReturn("GENERATED_PASSWORD");
        when(secrets.encode(anyString())).thenReturn("hash");
    }
    @Test void creationAndDuplicateName() {
        assertThat(service.create(admin, "sample", "Player").password()).isEqualTo("GENERATED_PASSWORD");
        doThrow(new DuplicateKeyException("duplicate")).when(db).createAccount(any(), any(), any(), any());
        assertThatThrownBy(() -> service.create(admin, "sample", "Player")).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("LOGIN_NAME_EXISTS"));
    }
    @Test void listsAndMissingAccounts() {
        var stats = new AccountStats(120, 100, 8);
        when(db.accountStats()).thenReturn(stats);
        assertThat(service.stats()).isEqualTo(stats);
        assertThat(service.account("id").loginName()).isEqualTo("sample");
        assertThatThrownBy(() -> service.account("missing")).isInstanceOf(Problem.class);
        service.list("sam", 1);
        service.sessions("id", 1);
        service.audits(1);
        verify(db).accounts("sam", 50);
        verify(db).sessions("id", 50);
        verify(audit).audits(50);
        assertThatThrownBy(() -> service.resetPassword(admin, "missing")).isInstanceOf(Problem.class);
    }
    @Test void accountMutationsInvalidateSessionsAndClearPresence() {
        assertThat(service.resetPassword(admin, "id").loginName()).isEqualTo("sample");
        service.setEnabled(admin, "id", false);
        service.setEnabled(admin, "id", true);
        service.revokeAll(admin, "id");
        verify(db).resetPassword("id", "hash");
        verify(db).setStatus("id", "ACTIVE");
        verify(db).setStatus("id", "DISABLED");
        verify(db).bumpEpoch("id");
        verify(db, times(4)).clearPresence("id");
        verify(admins, times(4)).requireManagement(admin);
    }
    @Test void sessionRevocationMustBelongToAccount() {
        assertThatThrownBy(() -> service.revoke(admin, "id", "session")).isInstanceOf(Problem.class);
        when(db.lockSession("session")).thenReturn(new Session("session", "other", "hash", 0, Instant.EPOCH, Instant.MAX, null, null));
        assertThatThrownBy(() -> service.revoke(admin, "id", "session")).isInstanceOf(Problem.class);
        when(db.lockSession("session")).thenReturn(new Session("session", "id", "hash", 0, Instant.EPOCH, Instant.MAX, null, null));
        service.revoke(admin, "id", "session");
        verify(db).revokeSession("session", "ADMIN_REVOKED");
        verify(db).clearSessionPresence("id", "session");
    }
    @Test void reservedNumericNamesAndQqPasswordResetAreRejected() {
        assertThatThrownBy(() -> service.create(admin, "12345", "Player")).isInstanceOfSatisfying(Problem.class,
                e -> assertThat(e.code()).isEqualTo("LOGIN_NAME_RESERVED"));
        when(db.lockAccount("id")).thenReturn(new Account("id", "12345", null, "ACTIVE", 0, Instant.EPOCH, "12345", "nick", "url"));
        assertThatThrownBy(() -> service.resetPassword(admin, "id")).isInstanceOfSatisfying(Problem.class,
                e -> assertThat(e.code()).isEqualTo("QQ_PASSWORD_UNSUPPORTED"));
        verify(db, never()).resetPassword(any(), any());
    }
    @Test void revocationPushTargetsMatchingSessionOnly() {
        var events = mock(SessionEvents.class);
        service = new AccountService(db, audit, admins, secrets, TestTransactions.immediate(), events);
        when(db.lockSession("session")).thenReturn(new Session("session", "id", "hash", 0, Instant.EPOCH, Instant.MAX, null, null));
        var owner = new Presence("id", "session", "connection", Instant.MAX);
        when(db.lockPresence("id")).thenReturn(owner);
        service.revoke(admin, "id", "session");
        verify(events).kick(owner, "ADMIN_REVOKED");
        reset(events);
        when(db.lockPresence("id")).thenReturn(new Presence("id", "other", "connection", Instant.MAX));
        service.revoke(admin, "id", "session");
        verifyNoInteractions(events);
    }
}
