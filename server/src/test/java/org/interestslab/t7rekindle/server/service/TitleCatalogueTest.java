package org.interestslab.t7rekindle.server.service;

import java.time.Instant;
import java.util.List;
import java.util.stream.IntStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.interestslab.t7rekindle.server.domain.Models.AdminIdentity;
import org.interestslab.t7rekindle.server.domain.TitleModels.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import static org.interestslab.t7rekindle.server.domain.TitleModels.ruleFor;

class TitleCatalogueTest {
    private final TitleMapper db = mock(TitleMapper.class);
    private final AdminMapper audit = mock(AdminMapper.class);
    private final AdminService admins = mock(AdminService.class);
    private final AdminIdentity admin = new AdminIdentity(0, false);
    private final TitleService service = new TitleService(db, mock(AuthMapper.class), audit,
            mock(AuthService.class), admins, TestTransactions.immediate());

    @BeforeEach void setup() {
        when(db.lockCatalogue()).thenReturn(List.of(1007, 1008, 1000000));
        when(db.definition(1007)).thenReturn(new Definition(1007, "首测元老", "", 0, true));
        when(db.definition(1000000)).thenReturn(new Definition(1000000, "重燃共建者", "共同续写战场", 2, true));
    }

    @Test void createsCustomTitleWithServerAllocatedIdUnderCatalogueLock() {
        var draft = new Draft("  社区贡献者  ", "  感谢参与建设  ", 3, true);
        assertThat(service.create(admin, draft)).isEqualTo(1000001);
        var order = inOrder(admins, db, audit);
        order.verify(admins).requireManagement(admin);
        order.verify(db).lockCatalogue();
        order.verify(db).create(1000001, new Draft("社区贡献者", "感谢参与建设", 3, true));
        order.verify(audit).audit("admin", "TITLE_CREATED", "1000001");
        when(db.lockCatalogue()).thenReturn(List.of(1007, 1008));
        assertThat(service.create(admin, draft)).isEqualTo(1000000);
    }

    @Test void rejectsInvalidNamesDescriptionsAndCapacityWithoutWriting() {
        for (String name : new String[]{null, " ", "长".repeat(16), "a".repeat(32), "😀", "<b>称号</b>", "a\nb"})
            assertThatThrownBy(() -> service.create(admin, new Draft(name, "介绍", 0, true))).isInstanceOf(Problem.class);
        for (String description : new String[]{null, "长".repeat(33), "😀", "<b>说明</b>", "a\u0000b"})
            assertThatThrownBy(() -> service.create(admin, new Draft("称号", description, 0, true))).isInstanceOf(Problem.class);
        when(db.lockCatalogue()).thenReturn(IntStream.range(1000000, 1000064).boxed().toList());
        assertThatThrownBy(() -> service.create(admin, new Draft("称号", "", 0, true)))
                .isInstanceOf(Problem.class).hasMessageContaining("64");
        when(db.lockCatalogue()).thenReturn(List.of(Integer.MAX_VALUE));
        assertThatThrownBy(() -> service.create(admin, new Draft("称号", "", 0, true))).isInstanceOf(Problem.class);
        verify(db, never()).create(anyInt(), any());
    }

    @Test void editsCustomNamesButPreservesOriginalNamesAndRules() {
        service.configure(admin, 1000000, new Draft("重燃共建者", "共同续写战场", 9, false));
        verify(db).configure(1000000, new Draft("重燃共建者", "共同续写战场", 9, false));
        verify(db).clearTitle(1000000);
        assertThatThrownBy(() -> service.configure(admin, 1007, new Draft("其他称号", "", 0, true)))
                .isInstanceOf(Problem.class);
        service.configure(admin, 1007, new Draft(null, "测试纪念", 0, true));
        verify(db).configure(1007, new Draft("首测元老", "测试纪念", 0, true));
        assertThat(service.definition(1000000).custom()).isTrue();
        assertThat(service.definition(1007).custom()).isFalse();
        assertThat(ruleFor(1000000)).contains("管理员授予", "永久");
        assertThat(ruleFor(1008)).contains("2026年10月");
        assertThatThrownBy(() -> service.definition(999)).isInstanceOf(Problem.class);
    }

    @Test void deniesCreationAndEditingWithoutManagement() {
        doThrow(Problem.missing()).when(admins).requireManagement(admin);
        assertThatThrownBy(() -> service.create(admin, new Draft("称号", "", 0, true))).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.configure(admin, 1000000, new Draft("称号", "", 0, true))).isInstanceOf(Problem.class);
        verify(db, never()).create(anyInt(), any());
        verify(db, never()).configure(anyInt(), any());
    }

    @Test void recipientLookupUsesBoundedEscapedAccountPrefix() {
        var recipient = new Recipient("account", "player_one", "玩家", "GRANTED", Instant.EPOCH, null, true);
        when(db.recipients(1000000, "player\\_", 50)).thenReturn(List.of(recipient));
        assertThat(service.recipients(1000000, "player_", 1)).containsExactly(recipient);
        assertThat(recipient.owned()).isTrue();
        assertThatThrownBy(() -> service.recipients(1000000, "%", 0)).isInstanceOf(Problem.class);
        assertThatThrownBy(() -> service.recipients(1000000, "", -1)).isInstanceOf(Problem.class);
    }
}
