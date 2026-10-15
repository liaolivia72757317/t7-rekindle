package org.interestslab.t7rekindle.server.web;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.interestslab.t7rekindle.server.config.SecurityConfig;
import org.interestslab.t7rekindle.server.domain.AnnouncementModels;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.domain.TitleModels;
import org.interestslab.t7rekindle.server.service.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest({AdminController.class, AnnouncementAdminController.class, TitleAdminController.class,
        QqGroupAdminController.class})
@Import({SecurityConfig.class, AdminUiTest.WebSecurity.class})
class AdminUiTest {
    @TestConfiguration @EnableWebSecurity
    static class WebSecurity { }
    private static final Instant NOW = Instant.parse("2026-10-15T08:00:00Z");
    private static final AdminIdentity ADMIN = new AdminIdentity(0, false);
    @Autowired MockMvc mvc;
    @MockitoBean AdminService admins;
    @MockitoBean AuthService auth;
    @MockitoBean AccountService accounts;
    @MockitoBean GameNameService names;
    @MockitoBean QqGroupAdminService groups;
    @MockitoBean AnnouncementService announcements;
    @MockitoBean TitleService titles;
    @MockitoBean SecretResults results;

    @BeforeEach void fixtures() {
        when(admins.current(any())).thenReturn(ADMIN);
        when(accounts.stats()).thenReturn(new AccountStats(1234, 1200, 42));
        when(announcements.latest()).thenReturn(new AnnouncementModels.Published(
                "sample-announcement", "服务维护通知", "维护完成后可重新登录。", "公告正文", NOW));
        when(groups.view()).thenReturn(new QqGroupAdminService.View(false, null, null, null, null,
                0, "KEY_MISSING", null, false));
        when(accounts.list(anyString(), anyInt())).thenReturn(List.of(
                new AccountView("sample-account", "player.example", "ACTIVE", NOW, null, null, NOW, true, null, null),
                new AccountView("sample-disabled", "player.disabled", "DISABLED", NOW)));
        when(accounts.account("sample-account")).thenReturn(new AccountView("sample-account", "player.example", "ACTIVE", NOW));
        when(accounts.sessions(anyString(), anyInt())).thenReturn(List.of(
                new SessionView("sample-session", NOW, NOW.plusSeconds(86400), "VALID", true, NOW.plusSeconds(90))));
        when(accounts.audits(anyInt())).thenReturn(List.of(new Audit(1, "admin", "ACCOUNT_CREATE", "sample-account", NOW)));
        when(announcements.list(anyInt())).thenReturn(List.of(new AnnouncementModels.Summary(
                "sample-announcement", "服务维护通知", true, NOW, NOW)));
        when(announcements.entry("sample-announcement")).thenReturn(new AnnouncementModels.Entry(
                "sample-announcement", "服务维护通知", "维护完成后可重新登录。", "## 维护安排\n请留意后续公告。", true, NOW, NOW, NOW));
        when(titles.definitions()).thenReturn(List.of(
                new TitleModels.Definition(1007, "首测元老", "测试纪念", 0, true),
                new TitleModels.Definition(1008, "内测精英", "测试纪念", 1, true)));
        when(titles.account("sample-account")).thenReturn(new TitleModels.Snapshot(List.of(
                new TitleModels.Item(1007, "首测元老", "测试纪念", "获取：管理员授予；永久。", 0, true, true, "ADMIN", NOW, null),
                new TitleModels.Item(1008, "内测精英", "测试纪念", "获取：2026年10月功能上线后登录；永久。", 1, true, false, null, null, null)), 1007));
        when(results.consume(any(), eq("sample-result"))).thenReturn(new AccountService.GeneratedPassword("player.example", "EXAMPLE_PASSWORD"));
    }

    @Test void gameNameRenameRequiresCsrfAndRetainsRejectedInput() throws Exception {
        String path = "/admin/accounts/sample-account/game-name";
        mvc.perform(post(path).session(session()).param("gameName", "新名称"))
                .andExpect(status().isForbidden());
        verifyNoInteractions(names);
        mvc.perform(post(path).session(session()).with(csrf()).param("gameName", "新名称"))
                .andExpect(redirectedUrl("/admin/accounts/sample-account"));
        verify(names).rename(ADMIN, "sample-account", "新名称");
        doThrow(GameNameRules.exists()).when(names).rename(ADMIN, "sample-account", "<b>占用</b>");
        String html = mvc.perform(post(path).session(session()).with(csrf()).param("gameName", "<b>占用</b>"))
                .andExpect(status().isConflict()).andExpect(view().name("account"))
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(html).contains("游戏名称已被使用", "&lt;b&gt;占用&lt;/b&gt;").doesNotContain("<b>占用</b>");
        assertThat(render("", "accounts")).contains("name=\"gameName\"", "游戏名称");
    }

    @Test void homeRendersAtBothPathsAndUsesContextAwareLinks() throws Exception {
        for (String path : List.of("/admin", "/admin/")) {
            String html = mvc.perform(get(path).session(session())).andExpect(status().isOk())
                    .andExpect(view().name("home")).andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
            assertThat(html).contains("管理概览", "账户总数", "在线账户", "当前公告", "QQ 群校验", "最近操作")
                    .doesNotContain("常用入口", "shortcuts-heading");
            export("home", html);
        }
        String html = render("/console", "");
        assertThat(html).contains("href=\"/console/admin\"", "href=\"/console/admin/accounts\"", "aria-current=\"page\"");
        export("context-home", html);
    }

    @Test void groupedNavigationAndBreadcrumbsFollowThePageHierarchy() throws Exception {
        String html = render("/console", "accounts/sample-account");
        assertThat(html).contains("服务维护", "玩家管理", "系统管理", "aria-label=\"面包屑\"",
                "href=\"/console/admin/audit?scope=player\"", "href=\"/console/admin/audit?scope=system\"",
                "href=\"/console/admin/accounts\"", "账户详情");
        assertThat(html.indexOf(">服务维护</button>")).isLessThan(html.indexOf(">玩家管理</button>"));
        assertThat(html.indexOf(">玩家管理</button>")).isLessThan(html.indexOf(">系统管理</button>"));
    }

    @Test void unfinishedAuditPagesShowTheirGroupWithoutLoadingRecords() throws Exception {
        for (String scope : List.of("player", "system")) {
            String html = render("/console", "audit?scope=" + scope);
            assertThat(html).contains(scope.equals("player") ? "玩家操作记录" : "系统操作记录",
                    "建设中", "href=\"/console/admin\"")
                    .doesNotContain("ui-pagination", "EXAMPLE_ACTION");
            assertThat(html).containsOnlyOnce("aria-label=\"面包屑\"");
            export("audit-" + scope, html);
        }
        verify(accounts, never()).audits(anyInt());
        mvc.perform(get("/admin/audit").param("scope", "invalid").session(session())).andExpect(status().isBadRequest());
        mvc.perform(get("/admin/audit").param("scope", "player")).andExpect(redirectedUrl("/admin/login"));
    }

    @Test void homeRequiresLoginAndPasswordChangeAndIsTheLoginDestination() throws Exception {
        for (String path : List.of("/admin", "/admin/")) {
            mvc.perform(get(path)).andExpect(redirectedUrl("/admin/login"));
        }
        when(admins.login(anyString(), anyString(), anyString())).thenReturn(ADMIN);
        mvc.perform(post("/admin/login").with(csrf()).param("loginName", "admin").param("password", "EXAMPLE_PASSWORD"))
                .andExpect(redirectedUrl("/admin"));
        var firstLogin = new AdminIdentity(0, true);
        when(admins.login(anyString(), anyString(), anyString())).thenReturn(firstLogin);
        mvc.perform(post("/admin/login").with(csrf()).param("loginName", "admin").param("password", "EXAMPLE_PASSWORD"))
                .andExpect(redirectedUrl("/admin/password"));
        when(admins.current(any())).thenReturn(firstLogin);
        mvc.perform(get("/admin").session(session())).andExpect(redirectedUrl("/admin/password"));
    }

    @Test void homeShowsRealSummariesEscapesTextAndLimitsRecentActivity() throws Exception {
        when(announcements.latest()).thenReturn(new AnnouncementModels.Published("sample-announcement",
                "<script>公告</script>", "<b>摘要</b>", "PRIVATE_BODY_NOT_ON_HOME", NOW));
        when(groups.view()).thenReturn(new QqGroupAdminService.View(true, "123456", "<b>示例群</b>", "12345",
                "示例管理员", 2, "READY", NOW, true));
        when(accounts.audits(0)).thenReturn(java.util.stream.IntStream.range(0, 8)
                .mapToObj(i -> new Audit(i, "admin", "ACCOUNT_CREATED", "recent-target-" + i, NOW)).toList());
        String html = render("", "");
        assertThat(html).contains(">1234<", ">1200<", ">42<", "&lt;script&gt;公告&lt;/script&gt;", "&lt;b&gt;摘要&lt;/b&gt;",
                "&lt;b&gt;示例群&lt;/b&gt;", "校验已开启", "最近检查可用", "recent-target-4")
                .doesNotContain("recent-target-5", "PRIVATE_BODY_NOT_ON_HOME", "<script>公告</script>");
        verify(accounts, never()).list(anyString(), anyInt());
        export("home-populated", html);
    }

    @Test void homeHandlesEmptyDataAndGroupCredentialProblems() throws Exception {
        when(accounts.stats()).thenReturn(new AccountStats(0, 0, 0));
        when(announcements.latest()).thenReturn(null);
        when(accounts.audits(0)).thenReturn(List.of());
        String empty = render("", "");
        assertThat(empty).contains("暂无已发布公告", "暂无操作记录", "校验已关闭", "请配置加密密钥");
        export("home-empty", empty);
        when(groups.view()).thenReturn(new QqGroupAdminService.View(false, null, null, null, null, 0, "UNCONFIGURED", null, true));
        assertThat(render("", "")).contains("尚未扫码登录");
        when(groups.view()).thenReturn(new QqGroupAdminService.View(true, "123456", "示例群", "12345", "示例管理员",
                2, "EXPIRED", NOW, true));
        String expired = render("", "");
        assertThat(expired).contains("校验已开启", "请检查凭据或重新扫码").doesNotContain("最近检查可用");
        export("home-expired", expired);
    }

    @Test void managementPagesUseSharedShellLocalAssetsAndCsrf() throws Exception {
        for (String page : List.of("", "accounts", "accounts/sample-account", "accounts/sample-account/titles",
                "announcements", "announcements/new", "announcements/sample-announcement", "audit", "titles",
                "qq-group", "password", "results/sample-result", "ui-components")) {
            String html = render("", page);
            assertThat(html).contains("ui-scope", "ui-shell", "ui-topbar", "ui-mainnav", "id=\"main-content\"",
                    "ui-footer", "ui-tokens.css", "ui-tabler-adapter.css", "tabler/1.6.1/tabler.min.js", "_csrf", "aria-label=\"面包屑\"")
                    .doesNotContain("class=\"sidebar\"", "bootstrap.bundle", "fonts.googleapis", "<script>", "（UTC）");
            assertThat(html.indexOf("tabler.min.css")).isLessThan(html.indexOf("ui-tokens.css"));
            assertThat(html).containsOnlyOnce("aria-label=\"面包屑\"");
            assertThat(html.indexOf("ui-tokens.css")).isLessThan(html.indexOf("ui-tabler-adapter.css"));
        }
    }

    @Test void backNavigationUsesSecondaryButtonsAndKeepsDestinations() throws Exception {
        for (String[] entry : List.of(
                new String[]{"accounts/sample-account", "/console/admin/accounts"},
                new String[]{"accounts/sample-account/titles", "/console/admin/accounts/sample-account"},
                new String[]{"announcements/new", "/console/admin/announcements"},
                new String[]{"announcements/sample-announcement", "/console/admin/announcements"},
                new String[]{"results/sample-result", "/console/admin/accounts"})) {
            String html = render("/console", entry[0]);
            assertThat(html).containsPattern("<a class=\"btn ui-btn-secondary admin-back\"[^>]*href=\"" + entry[1] + "\"");
        }
    }

    @Test void passwordAndResultPanelsUseFullContentWidth() throws Exception {
        for (String page : List.of("password", "results/sample-result", "results/expired-result")) {
            assertThat(render("", page)).contains("class=\"ui-container ui-main\"", "class=\"card admin-panel\"")
                    .doesNotContain("admin-narrow");
        }
    }

    @Test void timestampsUseSharedLocalTimeMarkupAndKeepEmptyValues() throws Exception {
        for (String page : List.of("", "accounts", "accounts/sample-account", "accounts/sample-account/titles",
                "announcements", "announcements/sample-announcement", "audit")) {
            assertThat(render("", page)).contains("<time", "datetime=\"2026-10-", "data-local-time")
                    .doesNotContain("（UTC）", "时间为 UTC");
        }
        String home = render("", "");
        assertThat(home).contains("尚未验证").doesNotContain("datetime=\"null\"", "查看账户状态、当前公告与最近操作", "包含已启用和已禁用账户");
    }

    @Test void anonymousLoginAndErrorsUseCompactAccessibleLayout() throws Exception {
        String login = mvc.perform(get("/admin/login")).andExpect(status().isOk()).andReturn().getResponse()
                .getContentAsString(StandardCharsets.UTF_8);
        assertThat(login).contains("ui-scope", "ui-auth-card", "_csrf").doesNotContain("id=\"main-navigation\"");
        export("login", login);
        when(admins.login(anyString(), anyString(), anyString())).thenThrow(Problem.unauthorized());
        mvc.perform(post("/admin/login").with(csrf()).param("loginName", "admin").param("password", "EXAMPLE_PASSWORD"))
                .andExpect(status().isUnauthorized()).andExpect(view().name("login"))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("data-error-summary")));
    }

    @Test void contextPathIsKeptInResourcesNavigationAndAuthenticationRedirects() throws Exception {
        String html = render("/console", "qq-group");
        assertThat(html).contains("/console/admin/assets/", "data-api-base=\"/console/admin/qq-group\"");
        mvc.perform(get("/console/admin/accounts").contextPath("/console"))
                .andExpect(redirectedUrl("/console/admin/login"));
        when(admins.current(any())).thenReturn(new AdminIdentity(0, true));
        mvc.perform(get("/console/admin/password").contextPath("/console").session(session())).andExpect(status().isOk());
        mvc.perform(get("/console/admin/accounts").contextPath("/console").session(session()))
                .andExpect(redirectedUrl("/console/admin/password"));
    }

    @Test void invalidTitleDraftIsRetainedAndEscaped() throws Exception {
        doThrow(Problem.invalid()).when(titles).configure(any(), anyInt(), any());
        String html = mvc.perform(post("/admin/titles/1008").session(session()).with(csrf())
                .param("description", "<script>示例</script>").param("sortOrder", "5"))
                .andExpect(status().isBadRequest()).andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(html).contains("&lt;script&gt;示例&lt;/script&gt;", "value=\"5\"", "data-error-summary");
    }

    @Test void titleCatalogueCreatesAndEditsCustomTitlesWithoutChangingOriginalNames() throws Exception {
        var custom = new TitleModels.Definition(1000000, "重燃共建者", "共同续写战场", 2, true);
        when(titles.definitions()).thenReturn(List.of(
                new TitleModels.Definition(1007, "首测元老", "测试纪念", 0, true), custom));
        String html = mvc.perform(get("/console/admin/titles").contextPath("/console").session(session()))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(html).contains("新增称号", "重燃共建者", "/console/admin/titles/1000000/grants", "创建称号");
        export("titles", html.replace("/console/", "/"));
        mvc.perform(post("/admin/titles").session(session())).andExpect(status().isForbidden());
        when(titles.create(any(), any())).thenReturn(1000001);
        mvc.perform(post("/admin/titles").session(session()).with(csrf()).param("name", "社区贡献者")
                .param("description", "共同建设社区").param("sortOrder", "3").param("enabled", "true"))
                .andExpect(redirectedUrl("/admin/titles?created"));
        verify(titles).create(ADMIN, new TitleModels.Draft("社区贡献者", "共同建设社区", 3, true));
        String editing = mvc.perform(get("/admin/titles").session(session()).param("edit", "1000000"))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(editing).contains("编辑称号", "value=\"重燃共建者\"", "保存称号").doesNotContain("readonly=");
        mvc.perform(post("/admin/titles/1000000").session(session()).with(csrf()).param("name", "社区共建者")
                .param("description", "感谢贡献").param("sortOrder", "4"))
                .andExpect(redirectedUrl("/admin/titles?saved"));
        verify(titles).configure(ADMIN, 1000000, new TitleModels.Draft("社区共建者", "感谢贡献", 4, false));
        mvc.perform(get("/admin/titles").session(session()).param("edit", "1007"))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("readonly=")));
        when(titles.create(any(), any())).thenThrow(Problem.invalid());
        String failed = mvc.perform(post("/admin/titles").session(session()).with(csrf()).param("name", "<b>名称</b>")
                .param("description", "保留介绍").param("sortOrder", "8"))
                .andExpect(status().isBadRequest()).andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(failed).contains("&lt;b&gt;名称&lt;/b&gt;", "保留介绍", "value=\"8\"", "data-error-summary");
    }

    @Test void titleGrantPageShowsOwnershipAndProtectsAccountActions() throws Exception {
        var custom = new TitleModels.Definition(1000000, "重燃共建者", "共同续写战场", 2, false);
        when(titles.definition(1000000)).thenReturn(custom);
        when(titles.recipients(eq(1000000), anyString(), anyInt())).thenReturn(List.of(
                new TitleModels.Recipient("sample-account", "player.example", "社区玩家", "GRANTED", NOW, null, true),
                new TitleModels.Recipient("sample-other", "player.other", "<b>昵称</b>", null, null, null, false)));
        String path = "/admin/titles/1000000/grants";
        mvc.perform(get(path)).andExpect(redirectedUrl("/admin/login"));
        String html = mvc.perform(get("/console" + path).contextPath("/console").session(session()).param("query", "player"))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(html).contains("class=\"btn ui-btn-secondary admin-back\" href=\"/console/admin/titles\"");
        assertThat(html).contains("暂停展示", "已佩戴", "未获得", "&lt;b&gt;昵称&lt;/b&gt;",
                "/console" + path + "/sample-account/revoke", "/console" + path + "/sample-other/grant");
        export("title-grants", html.replace("/console/", "/"));
        for (String action : List.of("grant", "revoke")) {
            mvc.perform(post(path + "/sample-account/" + action).session(session())).andExpect(status().isForbidden());
            mvc.perform(post(path + "/sample-account/" + action).session(session()).with(csrf()).param("query", "player_").param("page", "1"))
                    .andExpect(redirectedUrl(path + "?saved&query=player_&page=1"));
        }
        verify(titles).grant(ADMIN, "sample-account", 1000000);
        verify(titles).revoke(ADMIN, "sample-account", 1000000);
        when(titles.recipients(eq(1000000), anyString(), anyInt())).thenReturn(List.of());
        mvc.perform(get(path).session(session())).andExpect(content().string(org.hamcrest.Matchers.containsString("没有匹配账户")));
    }

    @Test void operationFormsRenderInsideLabelledModals() throws Exception {
        for (String page : List.of("accounts", "titles")) {
            String html = render("/console", page);
            String id = page.equals("accounts") ? "account-create" : "title-editor";
            assertThat(html).contains("id=\"" + id + "\"", "aria-labelledby=\"" + id + "-label\"",
                    "data-form-modal", "modal-dialog-scrollable", "data-bs-dismiss=\"modal\"");
            int modal = html.indexOf("id=\"" + id + "\"");
            int form = html.indexOf("data-dirty", modal);
            assertThat(form).isGreaterThan(modal);
            assertThat(html.substring(modal, form)).contains("modal-body");
            assertThat(html.substring(form)).contains("_csrf");
            assertThat(html).contains("data-modal-auto=\"false\"");
        }
        assertThat(render("", "titles?edit=1007")).contains("data-modal-auto=\"true\"");
    }

    @Test void announcementEditorStaysInlineForNewExistingAndSavedDrafts() throws Exception {
        for (String page : List.of("new", "sample-announcement", "sample-announcement?saved",
                "sample-announcement?published", "sample-announcement?unpublished")) {
            String html = render("/console", "announcements/" + page);
            assertThat(html).contains("编辑稿", "name=\"title\"", "name=\"summary\"", "name=\"bodyMarkdown\"",
                    "data-dirty", "data-async", "_csrf", "保存草稿")
                    .doesNotContain("id=\"announcement-editor\"", "data-form-modal", "data-modal-auto", "在模态框中编辑公告");
            if (page.equals("new")) {
                assertThat(html).contains("action=\"/console/admin/announcements\"").doesNotContain("发布公告</button>");
            } else {
                assertThat(html).contains("action=\"/console/admin/announcements/sample-announcement\"",
                        "formaction=\"/console/admin/announcements/sample-announcement/publish\"", "value=\"服务维护通知\"");
            }
        }
    }

    @Test void announcementEditorLoadsLocalMarkdownAssetsOnlyOnItsOwnPage() throws Exception {
        String html = render("/console", "announcements/new");
        assertThat(html).contains("/console/admin/assets/vendor/easymde/2.21.0/easymde.min.js",
                "/console/admin/assets/vendor/easymde/2.21.0/easymde.min.css",
                "/console/admin/assets/announcement-editor.js", "/console/admin/assets/announcement-preview.js",
                "/console/admin/assets/announcement-editor.css", "data-markdown-editor", "maxlength=\"65536\"")
                .doesNotContain("cdn.jsdelivr.net", "unpkg.com");
        assertThat(render("", "announcements")).doesNotContain("easymde", "announcement-editor.js");
        for (String asset : List.of("vendor/easymde/2.21.0/easymde.min.js", "vendor/easymde/2.21.0/easymde.min.css",
                "announcement-editor.js", "announcement-preview.js", "announcement-editor.css")) {
            mvc.perform(get("/admin/assets/" + asset)).andExpect(status().isOk());
        }
    }

    @Test void invalidAnnouncementDraftStaysInlineAndRetainsInput() throws Exception {
        doThrow(Problem.invalid()).when(announcements).save(any(), anyString(), any(), anyBoolean());
        String html = mvc.perform(post("/admin/announcements/sample-announcement").session(session()).with(csrf())
                .param("title", "<b>修改标题</b>").param("summary", "修改摘要").param("bodyMarkdown", "## 修改正文"))
                .andExpect(status().isBadRequest()).andExpect(view().name("announcement"))
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(html).contains("&lt;b&gt;修改标题&lt;/b&gt;", "修改摘要", "## 修改正文", "data-error-summary")
                .doesNotContain("id=\"announcement-editor\"", "data-form-modal", "data-modal-auto");
    }

    @Test void invalidAccountNameStaysInTheCreationForm() throws Exception {
        when(accounts.create(any(), anyString(), anyString())).thenThrow(Problem.invalid());
        mvc.perform(post("/admin/accounts").session(session()).with(csrf()).param("loginName", "bad name").param("gameName", "Player"))
                .andExpect(status().isBadRequest()).andExpect(view().name("accounts"))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("value=\"bad name\"")))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("data-modal-auto=\"true\"")));
    }

    @Test void emptyListsAndConfiguredGroupsRenderTheirOwnStates() throws Exception {
        when(accounts.list(anyString(), anyInt())).thenReturn(List.of());
        String empty = render("", "accounts");
        assertThat(empty).contains("没有匹配账户", "本页 0 条");
        export("accounts-empty", empty);
        fixtures();
        render("", "accounts");
        when(groups.view()).thenReturn(new QqGroupAdminService.View(true, "123456", "示例群", "12345", "示例管理员",
                2, "READY", NOW, true));
        String configured = render("", "qq-group");
        assertThat(configured).contains("最近检查可用", "关闭入群校验", "data-qq=\"12345\"");
        export("qq-group-ready", configured);
        fixtures();
        render("", "qq-group");
    }

    @Test void passwordErrorsStayOnTheFormWithoutEchoingPasswords() throws Exception {
        String html = mvc.perform(post("/admin/password").session(session()).with(csrf())
                .param("oldPassword", "EXAMPLE_OLD_PASSWORD").param("newPassword", "EXAMPLE_NEW_PASSWORD")
                .param("confirmation", "EXAMPLE_DIFFERENT_PASSWORD"))
                .andExpect(status().isBadRequest()).andExpect(view().name("password"))
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(html).contains("两次新密码不一致", "data-error-summary")
                .doesNotContain("EXAMPLE_OLD_PASSWORD", "EXAMPLE_NEW_PASSWORD", "EXAMPLE_DIFFERENT_PASSWORD");
    }

    @Test void assetsArePublicButComponentsAndMutationsRemainProtected() throws Exception {
        for (String resource : List.of("ui-tokens.css", "ui-tabler-adapter.css", "ui-core.js", "admin.js",
                "vendor/tabler/1.6.1/tabler.min.css", "vendor/tabler/1.6.1/tabler.min.js", "icons.svg", "brand.ico")) {
            mvc.perform(get("/admin/assets/" + resource)).andExpect(status().isOk());
        }
        mvc.perform(get("/admin/ui-components")).andExpect(redirectedUrl("/admin/login"));
        mvc.perform(post("/admin/accounts").session(session()).param("loginName", "sample"))
                .andExpect(status().isForbidden());
    }

    private String render(String context, String page) throws Exception {
        String html = mvc.perform(get(context + "/admin/" + page).contextPath(context).session(session()))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        export((context.isEmpty() ? "" : "context-") + page.replace('/', '-').replace('?', '-').replace('&', '-'), html);
        return html;
    }

    private void export(String name, String html) throws Exception {
        String output = System.getProperty("t7r.uiOutput");
        if (output == null) return;
        Path directory = Path.of(output);
        Files.createDirectories(directory);
        Files.writeString(directory.resolve(name + ".html"), html, StandardCharsets.UTF_8);
    }

    private MockHttpSession session() {
        var session = new MockHttpSession();
        session.setAttribute(SecurityConfig.ADMIN_IDENTITY, ADMIN);
        return session;
    }
}
