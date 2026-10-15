package org.interestslab.t7rekindle.server.service;

import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionTemplate;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.TitleModels.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class TitleServiceTest {
    private final TitleMapper db = mock(TitleMapper.class);
    private final AuthMapper accounts = mock(AuthMapper.class);
    private final AdminMapper audit = mock(AdminMapper.class);
    private final AuthService auth = mock(AuthService.class);
    private final AdminService admins = mock(AdminService.class);
    private final TransactionTemplate tx = TestTransactions.immediate();
    private final Instant now = Instant.parse("2026-10-15T00:00:00Z");
    private final Identity identity = new Identity("account", "session", "player", now.plusSeconds(90));
    private final AdminIdentity admin = new AdminIdentity(0, false);
    private final Definition elite = new Definition(1008, "内测精英", "测试纪念", 1, true);
    private TitleService service;

    @BeforeEach void setup() {
        service = new TitleService(db, accounts, audit, auth, admins, tx);
        when(db.definitions()).thenReturn(List.of(elite));
        when(db.definition(1008)).thenReturn(elite);
        when(accounts.now()).thenReturn(now);
        when(auth.requireLocked(identity)).thenReturn(identity);
        when(accounts.lockPresence("account")).thenReturn(new Presence("account", "session", "connection", now.plusSeconds(45)));
        when(accounts.lockAccount("account")).thenReturn(new Account("account", "player", "", "ACTIVE", 0, now));
        when(db.grants("account")).thenReturn(List.of());
        when(db.equipped("account")).thenReturn(null);
    }

    @Test void rewardWindowUsesBeijingBoundariesAndDoesNotBackfill() {
        for (String time : List.of("2026-09-30T15:59:59.999Z", "2026-10-31T16:00:00Z"))
            service.rewardOnline("account", Instant.parse(time));
        verify(db, never()).grant(anyString(), anyInt(), anyString(), any());
        for (String time : List.of("2026-09-30T16:00:00Z", "2026-10-31T15:59:59.999Z"))
            service.rewardOnline("account", Instant.parse(time));
        verify(db, times(2)).grant(eq("account"), eq(1008), eq("OCTOBER_2026_LOGIN"), any());
    }

    @Test void repeatLoginAndRevocationNeverRegrantOrReequip() {
        for (String state : List.of("GRANTED", "REVOKED")) {
            when(db.grantRecord("account", 1008)).thenReturn(new Grant(1008, state, "OCTOBER_2026_LOGIN", now, now));
            service.rewardOnline("account", now);
        }
        verify(db, never()).grant(anyString(), anyInt(), anyString(), any());
        verify(db, never()).equip(anyString(), any());
    }

    @Test void newRewardEquipsOnlyWhenEmptyAndVisible() {
        service.rewardOnline("account", now);
        verify(db).equip("account", 1008);
        when(db.equipped("account")).thenReturn(1007);
        service.rewardOnline("account", now);
        verify(db, times(1)).equip(anyString(), any());
        when(db.equipped("account")).thenReturn(null);
        when(db.definition(1008)).thenReturn(new Definition(1008, "内测精英", "", 1, false));
        service.rewardOnline("account", now);
        verify(db, times(1)).equip(anyString(), any());
    }

    @Test void equipRequiresLiveOwnerAndPossession() {
        assertThatThrownBy(() -> service.equip(identity, "other", 1008)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.equip(identity, "connection", 1008)).isInstanceOf(Problem.class);
        when(db.grantRecord("account", 1008)).thenReturn(new Grant(1008, "GRANTED", "ADMIN", now, null));
        service.equip(identity, "connection", 1008);
        verify(db).equip("account", 1008);
        service.equip(identity, "connection", null);
        verify(db).equip("account", null);
        when(accounts.lockPresence("account")).thenReturn(null);
        assertThatThrownBy(() -> service.current(identity, "connection")).isInstanceOf(Problem.class);
    }

    @Test void manualGrantRevocationAndVisibilityChangesAreAudited() {
        service.grant(admin, "account", 1008);
        verify(db).grant("account", 1008, "ADMIN", now);
        verify(audit).audit("admin", "TITLE_GRANTED", "account:1008");
        service.revoke(admin, "account", 1008);
        verify(db).revoke("account", 1008, now);
        verify(db).clearEquipped("account", 1008);
        service.configure(admin, 1008, new Draft(null, "测试纪念", 2, false));
        verify(db).clearTitle(1008);
        verify(audit).audit("admin", "TITLE_CONFIGURED", "1008");
    }

    @Test void originalTitleAcceptsItsExistingNameButRejectsRenaming() {
        service.configure(admin, 1008, new Draft("内测精英", "测试纪念", 2, true));
        verify(db).configure(eq(1008), argThat(draft -> draft.name().equals("内测精英")));
        assertThatThrownBy(() -> service.configure(admin, 1008, new Draft("新名称", "测试纪念", 2, true)))
                .isInstanceOfSatisfying(Problem.class,
                        error -> assertThat(error.code()).isEqualTo("ORIGINAL_TITLE_NAME"));
    }

    @Test void descriptionsFitOriginalClientAndRejectMarkupOrUnsupportedCharacters() {
        for (String text : List.of("长".repeat(33), "<b>说明</b>", "😀", "bad\u0000text"))
            assertThatThrownBy(() -> service.configure(admin, 1008, new Draft(null, text, 0, true))).isInstanceOf(Problem.class);
        service.configure(admin, 1008, new Draft(null, "", 0, true));
        verify(db).configure(eq(1008), any());
    }

    @Test void snapshotKeepsOwnershipButHidesInvalidSelections() {
        var veteran = new Definition(1007, "首测元老", "", 0, true);
        when(db.definitions()).thenReturn(List.of(veteran, elite));
        assertThat(service.definitions()).containsExactly(veteran, elite);
        assertThat(veteran.gameDescription()).contains("管理员授予");
        when(db.grants("account")).thenReturn(List.of(new Grant(1008, "GRANTED", "ADMIN", now, null)));
        when(db.equipped("account")).thenReturn(1008);
        assertThat(service.account("account").equippedTitleId()).isEqualTo(1008);
        when(db.equipped("account")).thenReturn(1007);
        assertThat(service.account("account").equippedTitleId()).isNull();
        when(db.equipped("account")).thenReturn(1008);
        when(db.definitions()).thenReturn(List.of(new Definition(1008, "内测精英", "", 1, false)));
        var disabled = service.account("account");
        assertThat(disabled.equippedTitleId()).isNull();
        assertThat(disabled.titles().getFirst().owned()).isTrue();
        when(db.grants("account")).thenReturn(List.of(new Grant(1008, "REVOKED", "ADMIN", now, now)));
        assertThat(service.account("account").titles().getFirst().revokedAt()).isEqualTo(now);
        assertThat(service.account("account").titles().getFirst().owned()).isFalse();
    }

    @Test void rejectsExpiredTakenOverDisabledRevokedAndUnknownTitles() {
        for (Presence presence : List.of(new Presence("account", "session", "connection", now),
                new Presence("account", "other", "connection", now.plusSeconds(45)))) {
            when(accounts.lockPresence("account")).thenReturn(presence);
            assertThatThrownBy(() -> service.current(identity, "connection")).isInstanceOf(Problem.class);
        }
        when(accounts.lockPresence("account")).thenReturn(new Presence("account", "session", "connection", now.plusSeconds(45)));
        when(db.grantRecord("account", 1008)).thenReturn(new Grant(1008, "REVOKED", "ADMIN", now, now));
        assertThatThrownBy(() -> service.equip(identity, "connection", 1008)).isInstanceOf(Problem.class);
        when(db.definition(1008)).thenReturn(new Definition(1008, "内测精英", "", 1, false));
        assertThatThrownBy(() -> service.equip(identity, "connection", 1008)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.equip(identity, "connection", 123)).isInstanceOf(Problem.class);
        when(accounts.lockAccount("account")).thenReturn(null);
        assertThatThrownBy(() -> service.grant(admin, "account", 1008)).isInstanceOf(Problem.class);
    }

    @Test void manualRegrantRequiresAnActualNewAwardAndManagement() {
        when(db.grantRecord("account", 1008)).thenReturn(new Grant(1008, "GRANTED", "ADMIN", now, null));
        service.grant(admin, "account", 1008);
        verify(db, never()).grant(anyString(), anyInt(), anyString(), any());
        when(db.grantRecord("account", 1008)).thenReturn(new Grant(1008, "REVOKED", "ADMIN", now, now));
        service.grant(admin, "account", 1008);
        verify(db).grant("account", 1008, "ADMIN", now);
        doThrow(Problem.missing()).when(admins).requireManagement(admin);
        assertThatThrownBy(() -> service.revoke(admin, "account", 1008)).isInstanceOf(Problem.class);
        verify(db, never()).revoke(anyString(), anyInt(), any());
    }

    @Test void configurationBoundsAndReenableNeverReequip() {
        for (Draft draft : List.of(new Draft(null, null, 0, true), new Draft(null, "text", -1, true),
                new Draft(null, "text", 10000, true), new Draft(null, "a>b", 0, true)))
            assertThatThrownBy(() -> service.configure(admin, 1008, draft)).isInstanceOf(Problem.class);
        service.configure(admin, 1008, new Draft(null, "长".repeat(32), 9999, true));
        verify(db, never()).clearTitle(anyInt());
        verify(db, never()).equip(anyString(), any());
    }
}
