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

class GameNameServiceTest {
    private final AuthMapper db = mock(AuthMapper.class);
    private final AdminMapper audit = mock(AdminMapper.class);
    private final AuthService auth = mock(AuthService.class);
    private final AdminService admins = mock(AdminService.class);
    private final QqGroupService groups = mock(QqGroupService.class);
    private final Identity identity = new Identity("id", "session", "player", Instant.MAX);
    private final AdminIdentity admin = new AdminIdentity(0, false);
    private final GameNameService service = new GameNameService(db, audit, auth, admins, groups, TestTransactions.immediate());

    @BeforeEach void setup() {
        when(auth.requireLocked(identity)).thenReturn(identity);
        when(db.lockAccount("id")).thenReturn(new Account("id", "player", "hash", "ACTIVE", 0, Instant.EPOCH,
                null, null, null, "Original"));
    }
    @Test void initializesUsingLockedIdentityAndAudits() {
        assertThat(service.initialize(identity, " Player ").gameName()).isEqualTo("Player");
        verify(auth).requireLocked(identity);
        verify(groups).require(null, false);
        verify(db).setGameName("id", "Player");
        verify(audit).audit("id", "ACCOUNT_GAME_NAME_SET", "id");
    }
    @Test void initializedNameIsIdempotentButSelfRenamingIsRejected() {
        when(auth.requireLocked(identity)).thenReturn(new Identity("id", "session", "player", Instant.MAX,
                null, null, null, null, "Player"));
        assertThat(service.initialize(identity, "Player").gameName()).isEqualTo("Player");
        assertCode(() -> service.initialize(identity, "Other"), "GAME_NAME_ALREADY_SET");
        verify(db, never()).setGameName(any(), any());
        verifyNoInteractions(audit);
    }
    @Test void duplicateNamesAreConflictsAndDoNotAudit() {
        doThrow(new DuplicateKeyException("uq_account_game_name")).when(db).setGameName(any(), any());
        assertCode(() -> service.initialize(identity, "Player"), "GAME_NAME_EXISTS");
        assertCode(() -> service.rename(admin, "id", "Player"), "GAME_NAME_EXISTS");
        verifyNoInteractions(audit);
    }
    @Test void administratorCanRenameButNotClear() {
        service.rename(admin, "id", " Player ");
        verify(admins).requireManagement(admin);
        verify(db).setGameName("id", "Player");
        verify(audit).audit("admin", "ACCOUNT_GAME_NAME_CHANGED", "id");
        assertCode(() -> service.rename(admin, "id", " "), "INVALID_GAME_NAME");
        assertCode(() -> service.rename(admin, "missing", "Player"), "NOT_FOUND");
    }
    @Test void unchangedAdminNameDoesNotWrite() {
        service.rename(admin, "id", "Original");
        verify(db, never()).setGameName(any(), any());
        verifyNoInteractions(audit);
    }
    @Test void rejectedIdentityAndGroupMembershipDoNotWrite() {
        when(auth.requireLocked(identity)).thenThrow(Problem.unauthorized());
        assertCode(() -> service.initialize(identity, "Player"), "INVALID_CREDENTIALS");
        doThrow(new Problem(403, "QQ_GROUP_NOT_MEMBER", "入群后重试")).when(groups).require(null, false);
        assertCode(() -> service.initialize(identity, "Player"), "QQ_GROUP_NOT_MEMBER");
        verifyNoInteractions(db, audit);
    }
    private static void assertCode(org.assertj.core.api.ThrowableAssert.ThrowingCallable call, String code) {
        assertThatThrownBy(call).isInstanceOfSatisfying(Problem.class, error -> assertThat(error.code()).isEqualTo(code));
    }
}
