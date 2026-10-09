package org.t7rekindle.server.service;

import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.dao.DataAccessResourceFailureException;
import org.t7rekindle.server.domain.Models.*;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.persistence.AdminMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AdminServiceTest {
    private final AdminMapper db = mock(AdminMapper.class);
    private final Secrets secrets = mock(Secrets.class);
    private final LoginLimiter limiter = mock(LoginLimiter.class);
    private final Admin admin = new Admin(1, "admin", "hash", false, 0, Instant.EPOCH);
    private final AdminIdentity identity = new AdminIdentity(0, false);
    private AdminService service;

    @BeforeEach void setup() {
        service = new AdminService(db, secrets, limiter, TestTransactions.immediate());
        when(db.admin()).thenReturn(admin);
        when(db.lockAdmin()).thenReturn(admin);
        when(secrets.randomToken()).thenReturn("RANDOM_PASSWORD");
        when(secrets.encode(anyString())).thenReturn("hash");
        when(secrets.matches("PASSWORD", "hash")).thenReturn(true);
    }
    @Test void bootstrapOnlyReturnsPasswordAfterWinningInsert() {
        assertThat(service.initialize()).isNull();
        when(db.admin()).thenReturn(null);
        assertThat(service.initialize()).isEqualTo("RANDOM_PASSWORD");
        verify(db).audit("system", "ADMIN_INITIALIZED", "admin");
    }
    @Test void bootstrapRacesDoNotResetExistingAdminAndFailuresAreNotAbsence() {
        when(db.admin()).thenReturn(null, admin);
        doThrow(new DuplicateKeyException("duplicate")).when(db).createAdmin("hash");
        assertThat(service.initialize()).isNull();
        when(db.admin()).thenReturn(null);
        assertThatThrownBy(service::initialize).isInstanceOf(DuplicateKeyException.class);
        when(db.admin()).thenThrow(new DataAccessResourceFailureException("offline"));
        assertThatThrownBy(service::initialize).isInstanceOf(DataAccessResourceFailureException.class);
        verify(db, never()).changePassword(any(), anyBoolean());
    }
    @Test void loginChecksNamePasswordAndMissingAdmin() {
        assertThat(service.login("admin", "PASSWORD", "IP")).isEqualTo(identity);
        assertThatThrownBy(() -> service.login("someone", "PASSWORD", "IP")).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.login("admin", "WRONG_PASSWORD", "IP")).isInstanceOf(Problem.class);
        when(db.lockAdmin()).thenReturn(null);
        assertThatThrownBy(() -> service.login("admin", "PASSWORD", "IP")).isInstanceOf(Problem.class);
        verify(db, times(3)).audit(eq("anonymous"), eq("ADMIN_LOGIN_FAILED"), any());
    }
    @Test void currentIdentityAndFirstPasswordGateAreEnforced() {
        assertThat(service.current(identity)).isEqualTo(identity);
        assertThatThrownBy(() -> service.current(null)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.current(new AdminIdentity(1, false))).isInstanceOf(Problem.class);
        when(db.admin()).thenReturn(null);
        assertThatThrownBy(() -> service.current(identity)).isInstanceOf(Problem.class);
        service.requireManagement(identity);
        when(db.lockAdmin()).thenReturn(new Admin(1, "admin", "hash", true, 0, Instant.EPOCH));
        assertThatThrownBy(() -> service.requireManagement(identity)).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("PASSWORD_CHANGE_REQUIRED"));
    }
    @Test void changingAndRecoveringPasswordsHaveExplicitDifferentFlags() {
        assertThatThrownBy(() -> service.changePassword(identity, "PASSWORD", "PASSWORD")).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.changePassword(identity, "WRONG_PASSWORD", "NEXT_PASSWORD")).isInstanceOf(Problem.class);
        service.changePassword(identity, "PASSWORD", "NEXT_PASSWORD");
        verify(db).changePassword("hash", false);
        assertThat(service.reset()).isEqualTo("RANDOM_PASSWORD");
        verify(db).changePassword("hash", true);
        when(db.lockAdmin()).thenReturn(null);
        assertThatThrownBy(service::reset).isInstanceOf(Problem.class);
    }
}
