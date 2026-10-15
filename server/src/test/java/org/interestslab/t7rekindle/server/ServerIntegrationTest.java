package org.interestslab.t7rekindle.server;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.mysql.MySQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.interestslab.t7rekindle.server.config.SecurityConfig;
import org.interestslab.t7rekindle.server.config.OnlinePushConfig;
import org.interestslab.t7rekindle.server.integration.QqClient;
import org.interestslab.t7rekindle.server.integration.QqGroupClient;
import org.interestslab.t7rekindle.server.integration.QqQrClient;
import org.interestslab.t7rekindle.server.integration.QqCookies;
import org.interestslab.t7rekindle.server.integration.QqCookieVault;
import org.interestslab.t7rekindle.server.domain.QqGroupModels.*;
import org.interestslab.t7rekindle.server.persistence.QqGroupMapper;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.AuthMapper;
import org.interestslab.t7rekindle.server.service.*;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

@SpringBootTest(properties = {"server.servlet.session.cookie.secure=false", "spring.flyway.enabled=true",
        "QQ_GROUP_COOKIE_KEY=TEST_KEY"})
@AutoConfigureMockMvc
class ServerIntegrationTest {
    static final MySQLContainer MYSQL = new MySQLContainer("mysql:8.4.11").withTmpFs(java.util.Map.of("/var/lib/mysql", "rw"));
    static final GenericContainer<?> REDIS = new GenericContainer<>(DockerImageName.parse("redis:8.6.6")).withExposedPorts(6379);
    static {
        MYSQL.start();
        REDIS.start();
    }
    @DynamicPropertySource static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", MYSQL::getJdbcUrl);
        registry.add("spring.datasource.username", MYSQL::getUsername);
        registry.add("spring.datasource.password", MYSQL::getPassword);
        registry.add("spring.data.redis.host", REDIS::getHost);
        registry.add("spring.data.redis.port", () -> REDIS.getMappedPort(6379));
    }
    @Autowired MockMvc mvc;
    @Autowired JsonMapper json;
    @Autowired JdbcTemplate jdbc;
    @Autowired Flyway flyway;
    @Autowired StringRedisTemplate redis;
    @Autowired Secrets secrets;
    @Autowired AdminService admins;
    @Autowired AccountService accounts;
    @Autowired AuthService auth;
    @Autowired QqAuthService qq;
    @Autowired SessionEvents events;
    @MockitoBean QqClient qqClient;
    @MockitoBean QqGroupClient groupClient;
    @MockitoBean QqQrClient qrClient;
    @Autowired QqGroupAdminService groupAdmin;
    @Autowired QqGroupMapper groupDb;
    @Autowired QqCookieVault cookieVault;
    @Autowired PresenceService presence;
    @Autowired TitleService titles;
    @Autowired AuthMapper db;
    @Autowired AdminMapper adminDb;
    @Autowired AnnouncementService announcements;
    @Autowired org.interestslab.t7rekindle.server.persistence.AnnouncementMapper announcementDb;
    @Autowired org.springframework.transaction.support.TransactionTemplate transactions;
    private static final String PASSWORD = "TEST_PASSWORD";
    private static final String NEXT_PASSWORD = "CHANGED_TEST_PASSWORD";
    private static final AdminIdentity ADMIN = new AdminIdentity(0, false);

    @Test void qqExchangeBindsMachineAndPushesOldConnectionAfterNewMachineLogin() throws Exception {
        String machine = "12345678-1234-1234-1234-123456789abc";
        String other = "12345678-1234-1234-1234-123456789def";
        String cookie = "uin=o12345; skey=TEST_SESSION";
        when(qqClient.getNick(cookie)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"测试昵称\"}})");
        var response = mvc.perform(post("/api/v1/auth/qq/sessions").contentType("application/json")
                .content(json.writeValueAsString(java.util.Map.of("machineId", machine, "qqSessionCookie", cookie, "gameName", "测试玩家"))))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.loginName").value("12345")).andReturn();
        var first = json.readValue(response.getResponse().getContentAsString(), QqSessionCredentials.class);
        mvc.perform(get("/api/v1/auth/session").header("Authorization", "Bearer " + first.sessionToken())).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/v1/auth/session").header("Authorization", "Bearer " + first.sessionToken()).header("X-Machine-Id", other))
                .andExpect(status().isUnauthorized());
        var identity = auth.authenticate(first.sessionToken(), machine);
        var online = presence.online(identity);
        var stream = mvc.perform(get("/api/v1/auth/online/" + online.connectionId() + "/events")
                .header("Authorization", "Bearer " + first.sessionToken()).header("X-Machine-Id", machine))
                .andExpect(request().asyncStarted()).andReturn();
        assertThat(stream.getResponse().getContentAsString()).contains("event:ready");
        var second = qq.login(other, cookie, "127.0.0.1");
        assertThat(second.accountId()).isEqualTo(first.accountId());
        assertThat(stream.getResponse().getContentAsString()).contains("event:kicked", "MACHINE_CHANGED", online.connectionId());
        mvc.perform(asyncDispatch(stream)).andExpect(status().isOk());
        assertThatThrownBy(() -> auth.authenticate(first.sessionToken(), machine)).isInstanceOf(Problem.class);
        assertThat(auth.authenticate(second.sessionToken(), other).nickname()).isEqualTo("测试昵称");
        assertThat(db.account(first.accountId()).passwordHash()).isNull();
        assertThatThrownBy(() -> accounts.resetPassword(ADMIN, first.accountId())).isInstanceOf(Problem.class);
    }

    @Test void legacyNumericPasswordAccountIsPreservedAndConcurrentQqRegistrationIsUnique() throws Exception {
        db.createAccount("00000000-0000-0000-0000-000000000001", "23456", secrets.encode(PASSWORD), "legacy");
        assertThat(auth.login("23456", PASSWORD, "IP")).isNotNull();
        String cookie = "uin=23456; skey=TEST_SESSION";
        when(qqClient.getNick(cookie)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"name\"}})");
        assertThatThrownBy(() -> qq.login("12345678-1234-1234-1234-123456789abc", cookie, "IP"))
                .isInstanceOfSatisfying(Problem.class, e -> assertThat(e.code()).isEqualTo("QQ_ACCOUNT_CONFLICT"));
        String concurrentCookie = "uin=34567; skey=TEST_SESSION";
        when(qqClient.getNick(concurrentCookie)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"name\"}})");
        try (var pool = Executors.newFixedThreadPool(4)) {
            var work = java.util.stream.IntStream.range(0, 4).<Callable<QqSessionCredentials>>mapToObj(i ->
                    () -> qq.login("12345678-1234-1234-1234-123456789abc", concurrentCookie, "IP", "Player")).toList();
            var ids = new java.util.HashSet<String>();
            for (var result : pool.invokeAll(work)) ids.add(result.get().accountId());
            assertThat(ids).hasSize(1);
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account WHERE qq_number='34567'", Integer.class)).isEqualTo(1);
    }

    @Test void committedKickIsRelayedToAnotherInstanceSubscriber() throws Exception {
        var received = new java.util.concurrent.CompletableFuture<KickEvent>();
        var subscriber = new OnlinePushConfig().sessionNotifications(redis.getConnectionFactory(),
                event -> received.complete((KickEvent) event), json);
        try {
            subscriber.afterPropertiesSet();
            subscriber.start();
            events.kick(new Presence("account", "session", "connection", java.time.Instant.MAX), "ADMIN_REVOKED");
            assertThat(received.get(5, java.util.concurrent.TimeUnit.SECONDS).connectionId()).isEqualTo("connection");
        } finally { subscriber.destroy(); }
    }

    @BeforeEach void reset() {
        jdbc.update("DELETE FROM account_title_selection");
        jdbc.update("DELETE FROM account_title");
        jdbc.update("DELETE FROM player_title WHERE title_id>1000000");
        jdbc.update("UPDATE player_title SET description='测试纪念',sort_order=CASE WHEN title_id=1000000 THEN 2 ELSE title_id-1007 END,enabled=TRUE");
        jdbc.update("UPDATE player_title SET name='重燃共建者' WHERE title_id=1000000");
        jdbc.update("DELETE FROM announcement");
        jdbc.update("DELETE FROM account_presence");
        jdbc.update("DELETE FROM auth_session");
        jdbc.update("DELETE FROM account");
        jdbc.update("DELETE FROM audit_log");
        jdbc.update("DELETE FROM qq_group_check");
        jdbc.update("UPDATE qq_group_config SET enabled=FALSE, group_number=NULL,group_name=NULL,qq_number=NULL,nickname=NULL,encrypted_cookie=NULL,policy_version=0,credential_version=0,health_status='MISSING',last_verified_at=NULL");
        jdbc.update("UPDATE admin_account SET password_hash=?,must_change_password=FALSE,auth_epoch=0", secrets.encode(PASSWORD));
        try (var connection = redis.getConnectionFactory().getConnection()) { connection.serverCommands().flushDb(); }
    }

    @Test void startupMigratesBeforeInitializingAdmin() {
        assertThat(flyway.info().applied()).extracting(migration -> migration.getScript())
                .containsExactly("V001__authentication.sql", "V002__administration.sql", "V003__qq_authentication.sql", "V004__qq_group_verification.sql", "V005__announcements.sql", "V006__account_titles.sql", "V007__custom_titles.sql", "V008__account_game_names.sql");
        assertThat(flyway.info().pending()).isEmpty();
        assertThat(adminDb.admin()).isNotNull();
        assertThat(admins.initialize()).isNull();
    }

    @Test void announcementDraftsPublishAtomicallyAndLatestFallsBackAfterUnpublish() throws Exception {
        var draft = new org.interestslab.t7rekindle.server.domain.AnnouncementModels.Draft("第一条", "摘要一", "正文一");
        mvc.perform(get("/api/v1/announcements/latest")).andExpect(status().isNoContent())
                .andExpect(header().string("Cache-Control", "no-store"));
        String first = announcements.create(ADMIN, draft);
        mvc.perform(get("/api/v1/announcements/latest")).andExpect(status().isNoContent());
        announcements.save(ADMIN, first, draft, true);
        jdbc.update("UPDATE announcement SET published_at='2020-01-01' WHERE announcement_id=?", first);
        var next = new org.interestslab.t7rekindle.server.domain.AnnouncementModels.Draft("第二条", "摘要二", "## 正文二");
        String second = announcements.create(ADMIN, next);
        announcements.save(ADMIN, second, next, true);
        announcements.save(ADMIN, second, draft, false);
        mvc.perform(get("/api/v1/announcements/latest").header("Authorization", "Bearer EXPIRED"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.announcementId").value(second))
                .andExpect(jsonPath("$.title").value("第二条")).andExpect(jsonPath("$.summary").value("摘要二"))
                .andExpect(jsonPath("$.bodyMarkdown").value("## 正文二"))
                .andExpect(jsonPath("$.publishedAt").exists()).andExpect(jsonPath("$.enabled").doesNotExist());
        announcements.unpublish(ADMIN, second);
        mvc.perform(get("/api/v1/announcements/latest")).andExpect(jsonPath("$.announcementId").value(first));
        announcements.save(ADMIN, second, draft, true);
        mvc.perform(get("/api/v1/announcements/latest")).andExpect(jsonPath("$.announcementId").value(second))
                .andExpect(jsonPath("$.title").value("第一条"));
        jdbc.update("UPDATE announcement SET published_at='2021-01-01'");
        mvc.perform(get("/api/v1/announcements/latest")).andExpect(jsonPath("$.announcementId").value(
                first.compareTo(second) > 0 ? first : second));
        announcements.unpublish(ADMIN, first);
        announcements.unpublish(ADMIN, second);
        mvc.perform(get("/api/v1/announcements/latest")).andExpect(status().isNoContent());
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM announcement", Integer.class)).isEqualTo(2);
        assertThat(adminDb.audits(0)).extracting(Audit::action).contains("ANNOUNCEMENT_CREATED",
                "ANNOUNCEMENT_DRAFT_SAVED", "ANNOUNCEMENT_PUBLISHED", "ANNOUNCEMENT_UNPUBLISHED");
        mvc.perform(get("/api/v1/auth/session")).andExpect(status().isUnauthorized());
        mvc.perform(post("/api/v1/announcements/latest")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/v1/announcements/other")).andExpect(status().isUnauthorized());
    }

    @Test void announcementAdminFormsRetainInvalidInputAndRequireAdminCsrfAndPasswordChange() throws Exception {
        var session = new MockHttpSession();
        session.setAttribute(SecurityConfig.ADMIN_IDENTITY, ADMIN);
        mvc.perform(get("/admin/announcements")).andExpect(redirectedUrl("/admin/login"));
        mvc.perform(get("/admin/announcements").session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("公告管理")));
        mvc.perform(get("/admin/announcements/new").session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("name=\"_csrf\"")));
        mvc.perform(post("/admin/announcements").session(session).param("title", "标题"))
                .andExpect(status().isForbidden());
        mvc.perform(post("/admin/announcements").session(session).with(csrf())
                .param("title", "保留标题").param("summary", "摘要").param("bodyMarkdown", ""))
                .andExpect(status().isBadRequest()).andExpect(model().attributeExists("error"))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("保留标题")));
        String location = mvc.perform(post("/admin/announcements").session(session).with(csrf())
                .param("title", "<script>test</script>").param("summary", "摘要").param("bodyMarkdown", "</textarea><script>test</script>"))
                .andExpect(status().is3xxRedirection()).andReturn().getResponse().getRedirectedUrl();
        String edit = location.split("\\?")[0];
        mvc.perform(get(edit).session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("&lt;script&gt;")))
                .andExpect(content().string(org.hamcrest.Matchers.not(org.hamcrest.Matchers.containsString("<script>test"))));
        mvc.perform(post(edit + "/publish").session(session).with(csrf())
                .param("title", "上线标题").param("summary", "上线摘要").param("bodyMarkdown", "上线正文"))
                .andExpect(status().is3xxRedirection());
        mvc.perform(post(edit).session(session).with(csrf()).param("title", "待发布")
                .param("summary", "草稿摘要").param("bodyMarkdown", "草稿正文")).andExpect(status().is3xxRedirection());
        mvc.perform(get("/api/v1/announcements/latest")).andExpect(jsonPath("$.title").value("上线标题"));
        mvc.perform(post(edit + "/publish").session(session).with(csrf()).param("title", "待发布")
                .param("summary", "").param("bodyMarkdown", "保留正文")).andExpect(status().isBadRequest())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("保留正文")));
        mvc.perform(post(edit + "/unpublish").session(session).with(csrf())).andExpect(status().is3xxRedirection());
        mvc.perform(get("/api/v1/announcements/latest")).andExpect(status().isNoContent());
        mvc.perform(get("/admin/announcements/missing").session(session)).andExpect(status().isNotFound());
        jdbc.update("UPDATE admin_account SET must_change_password=TRUE");
        mvc.perform(get("/admin/announcements").session(session)).andExpect(redirectedUrl("/admin/password"));
        mvc.perform(post(edit + "/unpublish").session(session).with(csrf())).andExpect(redirectedUrl("/admin/password"));
    }

    @Test void announcementWriteAndAuditRollBackTogether() {
        var draft = new org.interestslab.t7rekindle.server.domain.AnnouncementModels.Draft("标题", "摘要", "正文");
        String id = announcements.create(ADMIN, draft);
        var failedAudit = mock(AdminMapper.class);
        doThrow(new org.springframework.dao.DataAccessResourceFailureException("fixture audit error"))
                .when(failedAudit).audit(any(), any(), any());
        var service = new AnnouncementService(announcementDb, failedAudit, admins, transactions);
        var changed = new org.interestslab.t7rekindle.server.domain.AnnouncementModels.Draft("修改标题", "修改摘要", "修改正文");
        assertThatThrownBy(() -> service.save(ADMIN, id, changed, true))
                .isInstanceOf(org.springframework.dao.DataAccessResourceFailureException.class);
        assertThat(announcements.latest()).isNull();
        assertThat(announcements.entry(id).title()).isEqualTo("标题");
    }

    @Test void fixedExpiryAndExplicitTakeoverKeepBothCredentialsValid() {
        var created = accounts.create(ADMIN, "sample-user", "sample-user");
        var first = auth.login(created.loginName(), created.password(), "127.0.0.1");
        var a = auth.authenticate(first.sessionToken());
        var onlineA = presence.online(a);
        var second = auth.login(created.loginName(), created.password(), "127.0.0.1");
        var b = auth.authenticate(second.sessionToken());
        assertThat(presence.heartbeat(a, onlineA.connectionId())).isNotNull();
        var onlineB = presence.online(b);
        assertThatThrownBy(() -> presence.heartbeat(a, onlineA.connectionId())).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("ONLINE_TAKEN_OVER"));
        presence.offline(a, onlineA.connectionId());
        assertThat(presence.heartbeat(b, onlineB.connectionId())).isNotNull();
        assertThat(auth.authenticate(first.sessionToken())).isEqualTo(a);
        var replacement = presence.online(b);
        assertThatThrownBy(() -> presence.heartbeat(b, onlineB.connectionId())).isInstanceOf(Problem.class);
        presence.offline(b, replacement.connectionId());
        assertThat(auth.authenticate(second.sessionToken())).isEqualTo(b);
        var stored = db.sessionByHash(Secrets.hash(first.sessionToken()));
        assertThat(Duration.between(stored.createdAt(), stored.expiresAt())).isEqualTo(Duration.ofDays(30));
        assertThat(stored.tokenHash()).isNotEqualTo(first.sessionToken());
        assertThat(accounts.sessions(a.accountId(), 0)).allMatch(item -> item.credentialState().equals("VALID"));
    }

    @Test void expiryCannotBeRenewedByHeartbeatAndSessionDeadlineCapsLease() {
        var identity = accountIdentity("expiring-user");
        jdbc.update("UPDATE auth_session SET expires_at=UTC_TIMESTAMP(3)+INTERVAL 5 SECOND WHERE session_id=?", identity.sessionId());
        var connection = presence.online(identity);
        assertThat(connection.leaseExpiresAt()).isEqualTo(db.lockSession(identity.sessionId()).expiresAt());
        jdbc.update("UPDATE account_presence SET lease_expires_at=UTC_TIMESTAMP(3) WHERE account_id=?", identity.accountId());
        assertThatThrownBy(() -> presence.heartbeat(identity, connection.connectionId())).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("NOT_ONLINE"));
        jdbc.update("UPDATE auth_session SET created_at=UTC_TIMESTAMP(3)-INTERVAL 31 DAY,expires_at=UTC_TIMESTAMP(3)-INTERVAL 1 DAY WHERE session_id=?", identity.sessionId());
        assertThatThrownBy(() -> presence.online(identity)).isInstanceOf(Problem.class);
        assertThat(accounts.sessions(identity.accountId(), 0).getFirst().credentialState()).isEqualTo("EXPIRED");
    }

    @Test void concurrentOnlineRequestsHaveExactlyOneOwner() throws Exception {
        var identity = accountIdentity("concurrent-user");
        List<Callable<OnlineConnection>> work = new ArrayList<>();
        for (int i = 0; i < 100; i++) work.add(() -> presence.online(identity));
        try (var pool = Executors.newFixedThreadPool(12)) {
            var outcomes = pool.invokeAll(work);
            for (var outcome : outcomes) assertThat(outcome.get()).isNotNull();
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account_presence", Integer.class)).isEqualTo(1);
        assertThat(accounts.sessions(identity.accountId(), 0)).filteredOn(SessionView::online).hasSize(1);
    }

    @Test void revocationRacingOnlineAcquisitionNeverResurrectsPresence() throws Exception {
        var identity = accountIdentity("racing-user");
        List<Callable<Void>> work = new ArrayList<>();
        for (int i = 0; i < 40; i++) {
            work.add(() -> {
                try { presence.online(identity); }
                catch (Problem error) { assertThat(error.status()).isEqualTo(401); }
                return null;
            });
        }
        work.add(10, () -> { accounts.resetPassword(ADMIN, identity.accountId()); return null; });
        try (var pool = Executors.newFixedThreadPool(8)) {
            for (var result : pool.invokeAll(work)) result.get();
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account_presence", Integer.class)).isZero();
        assertThatThrownBy(() -> presence.online(identity)).isInstanceOf(Problem.class);
    }

    @Test void revocationResetAndDisableReleasePresenceWithoutRevivingOldTokens() {
        var identity = accountIdentity("managed-user");
        presence.online(identity);
        accounts.setEnabled(ADMIN, identity.accountId(), false);
        accounts.setEnabled(ADMIN, identity.accountId(), true);
        assertThatThrownBy(() -> presence.online(identity)).isInstanceOf(Problem.class);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account_presence", Integer.class)).isZero();
        var password = accounts.resetPassword(ADMIN, identity.accountId());
        var credentials = auth.login(password.loginName(), password.password(), "127.0.0.1");
        var fresh = auth.authenticate(credentials.sessionToken());
        presence.online(fresh);
        accounts.revoke(ADMIN, fresh.accountId(), fresh.sessionId());
        assertThatThrownBy(() -> auth.authenticate(credentials.sessionToken())).isInstanceOf(Problem.class);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM account_presence", Integer.class)).isZero();
        fresh = auth.authenticate(auth.login(password.loginName(), password.password(), "127.0.0.1").sessionToken());
        presence.online(fresh);
        accounts.revokeAll(ADMIN, fresh.accountId());
        assertThatThrownBy(() -> presence.online(identity)).isInstanceOf(Problem.class);
        assertThat(accounts.sessions(identity.accountId(), 0)).allMatch(item -> item.credentialState().equals("REVOKED"));
    }

    @Test void apiUsesBearerOnlyAndNeverCreatesBrowserSession() throws Exception {
        var created = accounts.create(ADMIN, "api-user", "api-user");
        String body = json.writeValueAsString(new org.interestslab.t7rekindle.server.web.ApiController.Login(created.loginName(), created.password()));
        var login = mvc.perform(post("/api/v1/auth/sessions").contentType("application/json").content(body))
                .andExpect(status().isCreated()).andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(cookie().doesNotExist("REKINDLE_ADMIN")).andReturn();
        var credentials = json.readValue(login.getResponse().getContentAsString(), SessionCredentials.class);
        String bearer = "Bearer " + credentials.sessionToken();
        mvc.perform(get("/api/v1/auth/session").header("Authorization", bearer)).andExpect(status().isOk()).andExpect(jsonPath("$.sessionToken").doesNotExist());
        var online = mvc.perform(post("/api/v1/auth/online").header("Authorization", bearer)).andExpect(status().isOk()).andReturn();
        String connectionId = json.readTree(online.getResponse().getContentAsString()).get("connectionId").asString();
        mvc.perform(post("/api/v1/auth/online/{id}/heartbeat", connectionId).header("Authorization", bearer)).andExpect(status().isOk());
        mvc.perform(delete("/api/v1/auth/online/{id}", connectionId).header("Authorization", bearer)).andExpect(status().isNoContent());
        mvc.perform(get("/admin/accounts").header("Authorization", bearer)).andExpect(redirectedUrl("/admin/login"));
        mvc.perform(get("/api/v1/auth/session").session(adminSession())).andExpect(status().isUnauthorized());
        mvc.perform(delete("/api/v1/auth/session").header("Authorization", bearer)).andExpect(status().isNoContent());
        mvc.perform(get("/api/v1/auth/session").header("Authorization", bearer)).andExpect(status().isUnauthorized());
        mvc.perform(post("/api/v1/auth/sessions").contentType("application/json").content("{"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("INVALID_INPUT"));
    }

    @Test void homeStatisticsCoverAllAccountsAndUseTheExistingOnlineRules() throws Exception {
        assertThat(accounts.stats()).isEqualTo(new AccountStats(0, 0, 0));
        String hash = secrets.encode(PASSWORD);
        var now = db.now();
        for (int i = 0; i < 60; i++) {
            String id = "home-" + String.format(java.util.Locale.ROOT, "%02d", i);
            db.createAccount(id, id, hash, id);
            if (i < 54) continue;
            var session = new Session(id, id, "hash-" + i, 0, now, now.plusSeconds(3600), null, null);
            db.createSession(session);
            db.savePresence(new Presence(id, id, "connection-" + i, now.plusSeconds(3600)));
        }
        jdbc.update("UPDATE account_presence SET lease_expires_at=UTC_TIMESTAMP(3) WHERE account_id='home-54'");
        jdbc.update("UPDATE auth_session SET expires_at=UTC_TIMESTAMP(3) WHERE session_id='home-55'");
        db.revokeSession("home-56", "ADMIN_REVOKED");
        db.bumpEpoch("home-57");
        jdbc.update("UPDATE account SET status='DISABLED' WHERE account_id='home-58'");
        assertThat(accounts.stats()).isEqualTo(new AccountStats(60, 59, 1));
        assertThat(accounts.list("home-", 0)).hasSize(50).noneMatch(AccountView::online);
        assertThat(accounts.list("home-", 1)).filteredOn(AccountView::online)
                .extracting(AccountView::loginName).containsExactly("home-59");
        mvc.perform(get("/admin").session(adminSession())).andExpect(status().isOk())
                .andExpect(model().attribute("stats", new AccountStats(60, 59, 1)));
    }

    @Test void embeddedPagesCsrfAndOneTimePasswordsWork() throws Exception {
        mvc.perform(get("/admin/login")).andExpect(status().isOk()).andExpect(content().string(org.hamcrest.Matchers.containsString("_csrf")));
        mvc.perform(get("/admin/assets/admin.css")).andExpect(status().isOk());
        mvc.perform(get("/admin/assets/mark.svg")).andExpect(status().isOk());
        mvc.perform(post("/admin/login").param("loginName", "admin").param("password", PASSWORD)).andExpect(status().isForbidden());
        MockHttpSession session = adminSession();
        mvc.perform(get("/admin/accounts").session(session)).andExpect(status().isOk());
        var created = mvc.perform(post("/admin/accounts").session(session).with(csrf()).param("loginName", "ui-user").param("gameName", "UI玩家"))
                .andExpect(status().is3xxRedirection()).andReturn();
        String resultUrl = created.getResponse().getRedirectedUrl();
        mvc.perform(get(resultUrl).session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("data-secret")));
        mvc.perform(get(resultUrl).session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.not(org.hamcrest.Matchers.containsString("data-secret"))));
        String id = accounts.list("ui-user", 0).getFirst().accountId();
        mvc.perform(get("/admin/accounts/{id}", id).session(session)).andExpect(status().isOk());
        mvc.perform(get("/admin/audit").session(session)).andExpect(status().isOk());
        mvc.perform(get("/admin/password").session(session)).andExpect(status().isOk());
        mvc.perform(post("/admin/accounts/{id}/status", id).session(session).with(csrf()).param("enabled", "false")).andExpect(status().is3xxRedirection());
        mvc.perform(post("/admin/accounts/{id}/password", id).session(session).with(csrf())).andExpect(status().is3xxRedirection());
        mvc.perform(post("/admin/accounts/{id}/sessions/revoke-all", id).session(session).with(csrf())).andExpect(status().is3xxRedirection());
        mvc.perform(get("/admin/accounts").session(session).param("page", "-1")).andExpect(status().isBadRequest());
    }

    @Test void firstLoginForcesPasswordChangeAndRotatesSession() throws Exception {
        jdbc.update("UPDATE admin_account SET must_change_password=TRUE");
        MockHttpSession anonymous = new MockHttpSession();
        var login = mvc.perform(post("/admin/login").session(anonymous).with(csrf()).param("loginName", "admin").param("password", PASSWORD))
                .andExpect(redirectedUrl("/admin/password")).andReturn();
        MockHttpSession authenticated = (MockHttpSession) login.getRequest().getSession(false);
        assertThat(anonymous.isInvalid()).isTrue();
        mvc.perform(get("/admin/accounts").session(authenticated)).andExpect(redirectedUrl("/admin/password"));
        mvc.perform(post("/admin/password").session(authenticated).with(csrf()).param("oldPassword", PASSWORD)
                .param("newPassword", NEXT_PASSWORD).param("confirmation", NEXT_PASSWORD))
                .andExpect(redirectedUrl("/admin/login?changed"));
        assertThat(authenticated.isInvalid()).isTrue();
        assertThatThrownBy(() -> admins.current(new AdminIdentity(0, true))).isInstanceOf(Problem.class);
        assertThat(admins.login("admin", NEXT_PASSWORD, "127.0.0.1").mustChangePassword()).isFalse();
    }

    @Test void bootstrapIsPersistentAndConcurrentInitializationHasOneWinner() throws Exception {
        assertThat(admins.initialize()).isNull();
        String before = adminDb.admin().passwordHash();
        assertThat(admins.initialize()).isNull();
        assertThat(adminDb.admin().passwordHash()).isEqualTo(before);
        jdbc.update("DELETE FROM admin_account");
        List<Callable<String>> initializers = new ArrayList<>();
        for (int i = 0; i < 8; i++) initializers.add(admins::initialize);
        int winners = 0;
        try (var pool = Executors.newFixedThreadPool(8)) {
            for (var result : pool.invokeAll(initializers)) if (result.get() != null) winners++;
        }
        assertThat(winners).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_log WHERE action='ADMIN_INITIALIZED'", Integer.class)).isEqualTo(1);
        String recovered = admins.reset();
        assertThat(admins.login("admin", recovered, "127.0.0.1").mustChangePassword()).isTrue();
    }

    @Test void realRedisRateLimitAndAuditNeverContainPasswords() {
        var created = accounts.create(ADMIN, "limited-user", "limited-user");
        for (int i = 0; i < 10; i++) {
            assertThatThrownBy(() -> auth.login(created.loginName(), "WRONG_PASSWORD", "127.0.0.1")).isInstanceOf(Problem.class);
        }
        assertThatThrownBy(() -> auth.login(created.loginName(), created.password(), "127.0.0.1"))
                .isInstanceOfSatisfying(Problem.class, error -> assertThat(error.status()).isEqualTo(429));
        assertThat(accounts.audits(0).toString()).doesNotContain(created.password(), "WRONG_PASSWORD", PASSWORD);
    }

    private Identity accountIdentity(String name) {
        var created = accounts.create(ADMIN, name, name);
        return auth.authenticate(auth.login(name, created.password(), "127.0.0.1").sessionToken());
    }
    @Test void groupAdminScansBeforeSelectionAndProtectsCandidateOwnership() throws Exception {
        var cookies = groupCookies();
        when(qrClient.start()).thenReturn(new QqQrClient.Ticket(cookies, new byte[] {1, 2}, "1"));
        when(qrClient.poll(any())).thenReturn(new QqQrClient.Poll("CONFIRMED", cookies));
        when(groupClient.identity(any())).thenReturn(new QqGroupClient.Result<>(new Administrator("54321", "测试管理员"), cookies));
        when(groupClient.groups(any())).thenReturn(new QqGroupClient.Result<>(List.of(new Group("123456", "测试群")), cookies));
        var admin = adminSession();
        mvc.perform(get("/admin/qq-group").session(admin)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("扫码登录群站")));
        mvc.perform(post("/admin/qq-group/qr").session(admin)).andExpect(status().isForbidden());
        var started = mvc.perform(post("/admin/qq-group/qr").session(admin).with(csrf())).andExpect(status().isOk()).andReturn();
        String id = json.readTree(started.getResponse().getContentAsString()).path("id").asString();
        mvc.perform(get("/admin/qq-group/qr/{id}/image", id).session(admin)).andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"));
        mvc.perform(post("/admin/qq-group/qr/{id}/poll", id).session(adminSession()).with(csrf()))
                .andExpect(status().isNotFound());
        mvc.perform(post("/admin/qq-group/qr/{id}/poll", id).session(admin).with(csrf()))
                .andExpect(jsonPath("$.state").value("READY")).andExpect(jsonPath("$.groups[0].number").value("123456"));
        assertThat(groupDb.policy().encryptedCookie()).isNull();
        mvc.perform(post("/admin/qq-group/settings").session(admin).with(csrf()).param("scanId", id)
                .param("groupNumber", "999999").param("version", "0").param("enabled", "true")).andExpect(status().isBadRequest());
        mvc.perform(post("/admin/qq-group/settings").session(admin).with(csrf()).param("scanId", id)
                .param("groupNumber", "123456").param("version", "0").param("enabled", "true")).andExpect(status().is3xxRedirection());
        assertThat(groupDb.policy().enabled()).isTrue();
        assertThat(groupDb.policy().encryptedCookie()).doesNotContain("TEST_GROUP_SECRET");
        assertThat(cookieVault.decrypt(groupDb.policy().encryptedCookie())).isEqualTo(cookies);
        var html = mvc.perform(get("/admin/qq-group").session(admin)).andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        assertThat(html).contains("测试群", "54321").doesNotContain("TEST_GROUP_SECRET", groupDb.policy().encryptedCookie());
        java.nio.file.Files.writeString(java.nio.file.Path.of("target/qq-group-preview.html"), html, java.nio.charset.StandardCharsets.UTF_8);
        mvc.perform(post("/admin/qq-group/groups").session(admin).with(csrf())).andExpect(jsonPath("$[0].number").value("123456"));
        mvc.perform(post("/admin/qq-group/check").session(admin).with(csrf())).andExpect(status().is3xxRedirection());
        mvc.perform(post("/admin/qq-group/disable").session(admin).with(csrf())).andExpect(status().is3xxRedirection());
        assertThat(groupDb.policy().enabled()).isFalse();
    }

    @Test void groupGateRechecksOnlineAndPersistsAccountTelemetryWithoutRevokingSession() throws Exception {
        var cookies = groupCookies();
        groupDb.configure(true, new Group("123456", "测试群"), new Administrator("54321", "管理员"), cookieVault.encrypt(cookies));
        String loginCookie = "uin=12345; skey=TEST_USER";
        String machine = "12345678-1234-1234-1234-123456789abc";
        when(qqClient.getNick(loginCookie)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"测试用户\"}})");
        when(groupClient.member(any(), eq("123456"), eq("12345"))).thenReturn(new QqGroupClient.Result<>(true, cookies));
        var session = qq.login(machine, loginCookie, "IP", "Player");
        var identity = auth.authenticate(session.sessionToken(), machine);
        var connection = presence.online(identity);
        assertThat(accounts.list("12345", 0).getFirst().lastHeartbeatAt()).isNull();
        presence.heartbeat(identity, connection.connectionId());
        var account = accounts.list("12345", 0).getFirst();
        assertThat(account.online()).isTrue();
        assertThat(account.lastHeartbeatAt()).isNotNull();
        assertThat(account.groupCheckedAt()).isNotNull();
        assertThat(account.groupCheckResult()).isEqualTo("MEMBER");
        verify(groupClient, times(1)).member(any(), eq("123456"), eq("12345"));
        assertThat(redis.getExpire("rekindle:qq-group:1:12345")).isBetween(880L, 900L);
        var checked = account.groupCheckedAt();
        mvc.perform(get("/api/v1/auth/session").header("Authorization", "Bearer " + session.sessionToken()).header("X-Machine-Id", machine))
                .andExpect(status().isOk());
        assertThat(accounts.list("12345", 0).getFirst().groupCheckedAt()).isEqualTo(checked);
        String accountsHtml = mvc.perform(get("/admin/accounts").session(adminSession())).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("最近入群检测"))).andReturn().getResponse().getContentAsString();
        java.nio.file.Files.writeString(java.nio.file.Path.of("target/qq-group-accounts-preview.html"), accountsHtml, java.nio.charset.StandardCharsets.UTF_8);

        redis.delete("rekindle:qq-group:1:12345");
        when(groupClient.member(any(), anyString(), anyString())).thenReturn(new QqGroupClient.Result<>(false, cookies));
        mvc.perform(post("/api/v1/auth/online/{id}/heartbeat", connection.connectionId())
                .header("Authorization", "Bearer " + session.sessionToken()).header("X-Machine-Id", machine))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("QQ_GROUP_NOT_MEMBER"))
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("123456")));
        account = accounts.list("12345", 0).getFirst();
        assertThat(account.online()).isFalse();
        assertThat(account.lastHeartbeatAt()).isNotNull();
        assertThat(account.groupCheckResult()).isEqualTo("NOT_MEMBER");
        assertThat(auth.authenticate(session.sessionToken(), machine)).isEqualTo(identity);

        redis.delete("rekindle:qq-group:1:12345");
        when(groupClient.member(any(), anyString(), anyString())).thenReturn(new QqGroupClient.Result<>(true, cookies));
        connection = presence.online(identity);
        redis.delete("rekindle:qq-group:1:12345");
        when(groupClient.member(any(), anyString(), anyString())).thenThrow(Problem.unavailable());
        presence.heartbeat(identity, connection.connectionId());
        assertThat(accounts.list("12345", 0).getFirst().online()).isTrue();
        assertThat(accounts.list("12345", 0).getFirst().groupCheckResult()).isEqualTo("ERROR");
        assertThatThrownBy(() -> presence.online(identity)).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.code()).isEqualTo("QQ_GROUP_UNAVAILABLE"));
        assertThatThrownBy(() -> qq.login(machine, loginCookie, "IP", "Player")).isInstanceOf(Problem.class);
        mvc.perform(get("/api/v1/auth/session").header("Authorization", "Bearer " + session.sessionToken()).header("X-Machine-Id", machine))
                .andExpect(status().isServiceUnavailable());

        groupAdmin.disable(ADMIN);
        presence.online(identity);
        assertThat(accounts.list("12345", 0).getFirst().groupCheckedAt()).isNull();
        mvc.perform(get("/admin/accounts").session(adminSession())).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.not(org.hamcrest.Matchers.containsString("最近入群检测"))));
        assertThat(jdbc.queryForObject("SELECT checked_at FROM qq_group_check WHERE qq_number='12345'", java.sql.Timestamp.class)).isNotNull();
        accounts.setEnabled(ADMIN, identity.accountId(), false);
        assertThatThrownBy(() -> presence.heartbeat(identity, "old")).isInstanceOf(Problem.class);
    }

    @Test void nonMemberLoginAuditsWithoutCreatingAccountOrIssuingSessions() throws Exception {
        var cookies = groupCookies();
        groupDb.configure(true, new Group("123456", "测试群"), new Administrator("54321", "管理员"), cookieVault.encrypt(cookies));
        String loginCookie = "uin=12345; skey=TEST_USER";
        String machine = "12345678-1234-1234-1234-123456789abc";
        when(qqClient.getNick(loginCookie)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"测试用户\"}})");
        when(groupClient.member(any(), eq("123456"), eq("12345"))).thenReturn(new QqGroupClient.Result<>(false, cookies));
        String request = json.writeValueAsString(java.util.Map.of("machineId", machine, "qqSessionCookie", loginCookie));
        for (int i = 0; i < 2; i++) {
            mvc.perform(post("/api/v1/auth/qq/sessions").contentType("application/json").content(request))
                    .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("QQ_GROUP_NOT_MEMBER"))
                    .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("123456")))
                    .andExpect(jsonPath("$.sessionToken").doesNotExist());
        }
        assertThat(accounts.list("12345", 0)).isEmpty();
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM auth_session", Integer.class)).isZero();
        var rejected = adminDb.audits(0).stream().filter(item -> item.action().equals("QQ_GROUP_NOT_MEMBER")).toList();
        assertThat(rejected).hasSize(2).allSatisfy(item -> {
            assertThat(item.actor()).isEqualTo("anonymous");
            assertThat(item.target()).contains("123456").doesNotContain("TEST_USER");
        });
        mvc.perform(get("/admin/audit").session(adminSession())).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("QQ_GROUP_NOT_MEMBER")));

        redis.delete("rekindle:qq-group:1:12345");
        when(groupClient.member(any(), eq("123456"), eq("12345"))).thenReturn(new QqGroupClient.Result<>(true, cookies));
        var session = qq.login(machine, loginCookie, "IP", "Player");
        assertThat(accounts.list("12345", 0)).hasSize(1);
        var identity = auth.authenticate(session.sessionToken(), machine);
        presence.online(identity);

        redis.delete("rekindle:qq-group:1:12345");
        when(groupClient.member(any(), eq("123456"), eq("12345"))).thenReturn(new QqGroupClient.Result<>(false, cookies));
        assertThatThrownBy(() -> qq.login("12345678-1234-1234-1234-123456789def", loginCookie, "IP"))
                .isInstanceOfSatisfying(Problem.class, error -> assertThat(error.code()).isEqualTo("QQ_GROUP_NOT_MEMBER"));
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM auth_session", Integer.class)).isEqualTo(1);
        assertThat(auth.authenticate(session.sessionToken(), machine)).isEqualTo(identity);
        assertThat(accounts.list("12345", 0).getFirst().online()).isFalse();
        assertThat(adminDb.audits(0).stream().filter(item -> item.action().equals("QQ_GROUP_NOT_MEMBER"))).hasSize(3);
    }

    @Test void groupPolicyVersionHidesOldChecksAndPreventsStaleCredentialWrites() {
        groupDb.configure(true, new Group("123456", "群一"), new Administrator("54321", "管理员"), cookieVault.encrypt(groupCookies()));
        var now = java.time.Instant.now();
        groupDb.record("12345", new Decision("123456", 1, "MEMBER", now));
        groupDb.configure(true, new Group("234567", "群二"), new Administrator("54321", "管理员"), cookieVault.encrypt(groupCookies()));
        assertThat(groupDb.refreshed(1, "STALE_CREDENTIAL")).isZero();
        groupDb.record("12345", new Decision("123456", 1, "ERROR", now.plusSeconds(2)));
        assertThat(jdbc.queryForObject("SELECT result FROM qq_group_check WHERE qq_number='12345'", String.class)).isEqualTo("MEMBER");
    }

    @Test void titleOwnershipApiRequiresOnlineOwnerAndPersistsUnequipAndRevocation() throws Exception {
        var created = accounts.create(ADMIN, "title-player", "title-player");
        var session = auth.login(created.loginName(), created.password(), "IP");
        var identity = auth.authenticate(session.sessionToken());
        var online = presence.online(identity);
        String path = "/api/v1/auth/online/" + online.connectionId() + "/titles";
        titles.revoke(ADMIN, identity.accountId(), 1008);
        titles.grant(ADMIN, identity.accountId(), 1007);
        mvc.perform(get(path)).andExpect(status().isUnauthorized());
        mvc.perform(get(path).header("Authorization", "Bearer " + session.sessionToken()))
                .andExpect(status().isOk()).andExpect(jsonPath("$.titles.length()").value(3))
                .andExpect(jsonPath("$.equippedTitleId").value(1007));
        mvc.perform(put(path + "/equipped").header("Authorization", "Bearer " + session.sessionToken())
                .contentType("application/json").content("{\"titleId\":1008}"))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("TITLE_NOT_AVAILABLE"));
        mvc.perform(put(path + "/equipped").header("Authorization", "Bearer " + session.sessionToken())
                .contentType("application/json").content("{\"titleId\":null}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.equippedTitleId").isEmpty());
        var next = presence.online(identity);
        assertThat(titles.current(identity, next.connectionId()).equippedTitleId()).isNull();
        assertThat(jdbc.queryForObject("SELECT state FROM account_title WHERE account_id=? AND title_id=1008",
                String.class, identity.accountId())).isEqualTo("REVOKED");
        mvc.perform(get(path).header("Authorization", "Bearer " + session.sessionToken()))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("ONLINE_TAKEN_OVER"));
        titles.grant(ADMIN, identity.accountId(), 1008);
        assertThat(titles.current(identity, next.connectionId()).equippedTitleId()).isEqualTo(1008);
        titles.configure(ADMIN, 1008, new org.interestslab.t7rekindle.server.domain.TitleModels.Draft(null, "测试奖励", 9, false));
        assertThat(titles.current(identity, next.connectionId()).equippedTitleId()).isNull();
        titles.configure(ADMIN, 1008, new org.interestslab.t7rekindle.server.domain.TitleModels.Draft(null, "测试奖励", 9, true));
        assertThat(titles.current(identity, next.connectionId()).equippedTitleId()).isNull();
        presence.offline(identity, next.connectionId());
        assertThatThrownBy(() -> titles.current(identity, next.connectionId())).isInstanceOf(Problem.class);
    }

    @Test void titleAdminPagesValidateConfigurationAndProtectMutations() throws Exception {
        var identity = accountIdentity("title-admin-test");
        var session = adminSession();
        String path = "/admin/accounts/" + identity.accountId() + "/titles";
        mvc.perform(get("/admin/titles")).andExpect(redirectedUrl("/admin/login"));
        mvc.perform(get("/admin/titles").session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("内测精英")));
        mvc.perform(post("/admin/titles/1008").session(session)).andExpect(status().isForbidden());
        mvc.perform(post("/admin/titles/1008").session(session).with(csrf())
                .param("description", "<b>text</b>").param("sortOrder", "0").param("enabled", "true"))
                .andExpect(status().isBadRequest());
        mvc.perform(post("/admin/titles/1008").session(session).with(csrf())
                .param("description", "新的介绍").param("sortOrder", "2"))
                .andExpect(redirectedUrl("/admin/titles?saved"));
        assertThat(titles.definitions().stream().filter(t -> t.titleId() == 1008).findFirst().orElseThrow().enabled()).isFalse();
        mvc.perform(post(path + "/1007/grant").session(session)).andExpect(status().isForbidden());
        mvc.perform(post(path + "/1007/grant").session(session).with(csrf())).andExpect(redirectedUrl(path));
        mvc.perform(get(path).session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("已佩戴")));
        mvc.perform(post(path + "/1007/revoke").session(session).with(csrf())).andExpect(redirectedUrl(path));
        mvc.perform(get(path).session(session)).andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("已撤销")));
    }

    @Test void titleCustomCatalogueFlowsThroughCreationRecipientsAndOwnership() throws Exception {
        var identity = accountIdentity("title-contributor");
        var session = adminSession();
        assertThat(titles.account(identity.accountId()).titles().stream().filter(t -> t.titleId() == 1000000)
                .findFirst().orElseThrow().owned()).isFalse();
        mvc.perform(post("/admin/titles").session(session).with(csrf()).param("name", "社区贡献者")
                .param("description", "感谢社区贡献").param("sortOrder", "3").param("enabled", "true"))
                .andExpect(redirectedUrl("/admin/titles?created"));
        assertThat(titles.definition(1000001).name()).isEqualTo("社区贡献者");
        String path = "/admin/titles/1000001/grants";
        mvc.perform(get(path).session(session).param("query", "title-contributor")).andExpect(status().isOk());
        mvc.perform(post(path + "/" + identity.accountId() + "/grant").session(session).with(csrf()))
                .andExpect(status().is3xxRedirection());
        assertThat(titles.recipients(1000001, "title-contributor", 0).getFirst().owned()).isTrue();
        var online = presence.online(identity);
        titles.equip(identity, online.connectionId(), 1000001);
        assertThat(titles.current(identity, online.connectionId()).equippedTitleId()).isEqualTo(1000001);
        mvc.perform(post("/admin/titles/1000001").session(session).with(csrf()).param("name", "重燃贡献者")
                .param("description", "共同建设").param("sortOrder", "4").param("enabled", "true"))
                .andExpect(redirectedUrl("/admin/titles?saved"));
        assertThat(titles.current(identity, online.connectionId()).titles().stream().filter(t -> t.titleId() == 1000001)
                .findFirst().orElseThrow().name()).isEqualTo("重燃贡献者");
        mvc.perform(post(path + "/" + identity.accountId() + "/revoke").session(session).with(csrf()))
                .andExpect(status().is3xxRedirection());
        assertThat(titles.current(identity, online.connectionId()).equippedTitleId()).isNull();
        assertThat(titles.recipients(1000001, "title-contributor", 0).getFirst().state()).isEqualTo("REVOKED");
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_log WHERE action='TITLE_CREATED'", Integer.class)).isEqualTo(1);
    }

    @Test void titleConcurrentCreationAllocatesDistinctIds() throws Exception {
        try (var pool = Executors.newFixedThreadPool(4)) {
            var tasks = java.util.stream.IntStream.range(0, 4).<Callable<Integer>>mapToObj(i -> () -> titles.create(ADMIN,
                    new org.interestslab.t7rekindle.server.domain.TitleModels.Draft("社区称号" + i, "", i, true))).toList();
            var ids = new java.util.HashSet<Integer>();
            for (var result : pool.invokeAll(tasks)) ids.add(result.get());
            assertThat(ids).containsExactlyInAnyOrder(1000001, 1000002, 1000003, 1000004);
        }
    }

    private QqCookies groupCookies() {
        return QqCookies.empty().accept(java.net.URI.create("https://qun.qq.com/"),
                List.of("skey=TEST_GROUP_SECRET; Domain=.qq.com; Path=/; Secure; HttpOnly"), java.time.Instant.now());
    }

    private MockHttpSession adminSession() {
        var session = new MockHttpSession();
        session.setAttribute(SecurityConfig.ADMIN_IDENTITY, ADMIN);
        return session;
    }
}
