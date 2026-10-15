package org.interestslab.t7rekindle.server;

import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.mysql.MySQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.integration.QqClient;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.AuthMapper;
import org.interestslab.t7rekindle.server.service.*;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest(properties = {"spring.flyway.enabled=true", "QQ_GROUP_COOKIE_KEY=TEST_KEY"})
@AutoConfigureMockMvc
class GameNameIntegrationTest {
    static final MySQLContainer MYSQL = new MySQLContainer("mysql:8.4.11").withTmpFs(Map.of("/var/lib/mysql", "rw"));
    static final GenericContainer<?> REDIS = new GenericContainer<>(DockerImageName.parse("redis:8.6.6")).withExposedPorts(6379);
    static { MYSQL.start(); REDIS.start(); }
    @DynamicPropertySource static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", MYSQL::getJdbcUrl);
        registry.add("spring.datasource.username", MYSQL::getUsername);
        registry.add("spring.datasource.password", MYSQL::getPassword);
        registry.add("spring.data.redis.host", REDIS::getHost);
        registry.add("spring.data.redis.port", () -> REDIS.getMappedPort(6379));
    }
    private static final String MACHINE = "12345678-1234-1234-1234-123456789abc";
    private static final String COOKIE = "uin=12345; skey=TEST_COOKIE";
    private static final AdminIdentity ADMIN = new AdminIdentity(0, false);
    @Autowired MockMvc mvc;
    @Autowired JsonMapper json;
    @Autowired JdbcTemplate jdbc;
    @Autowired AuthMapper db;
    @Autowired AuthService auth;
    @Autowired AccountService accounts;
    @Autowired AdminService admins;
    @Autowired Secrets secrets;
    @Autowired GameNameService names;
    @Autowired TransactionTemplate tx;
    @Autowired org.springframework.data.redis.core.StringRedisTemplate redis;
    @MockitoBean QqClient qq;
    @MockitoBean QqGroupService groups;

    @BeforeEach void reset() {
        jdbc.update("DELETE FROM account_title_selection");
        jdbc.update("DELETE FROM account_title");
        jdbc.update("DELETE FROM account_presence");
        jdbc.update("DELETE FROM auth_session");
        jdbc.update("DELETE FROM account");
        jdbc.update("DELETE FROM audit_log");
        jdbc.update("UPDATE admin_account SET must_change_password=FALSE,auth_epoch=0");
        try (var connection = redis.getConnectionFactory().getConnection()) { connection.serverCommands().flushDb(); }
        when(qq.getNick(COOKIE)).thenReturn("t7QqNick({\"retcode\":0,\"result\":{\"nick\":\"QQ昵称\"}})");
    }
    @Test void registrationCreatesNothingUntilNameIsAcceptedAndNeverSelfRenames() throws Exception {
        mvc.perform(post("/api/v1/auth/qq/sessions").contentType("application/json")
                .content(json.writeValueAsString(Map.of("machineId", MACHINE, "qqSessionCookie", COOKIE))))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("REGISTRATION_REQUIRED"));
        assertThat(count("account")).isZero();
        assertThat(count("auth_session")).isZero();
        mvc.perform(post("/api/v1/auth/qq/sessions").contentType("application/json").content(registration("😀")))
                .andExpect(status().isBadRequest());
        assertThat(count("account")).isZero();
        var result = mvc.perform(post("/api/v1/auth/qq/sessions").contentType("application/json").content(registration(" Player ")))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.gameName").value("Player"))
                .andExpect(jsonPath("$.nickname").value("QQ昵称")).andReturn();
        var session = json.readValue(result.getResponse().getContentAsString(), QqSessionCredentials.class);
        mvc.perform(post("/api/v1/auth/qq/sessions").contentType("application/json").content(registration("Other")))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.gameName").value("Player"));
        assertThat(count("account")).isEqualTo(1);
        mvc.perform(get("/api/v1/auth/game-name").header("Authorization", "Bearer " + session.sessionToken()))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/v1/auth/game-name").header("Authorization", "Bearer " + session.sessionToken()).header("X-Machine-Id", MACHINE))
                .andExpect(status().isOk()).andExpect(jsonPath("$.gameName").value("Player"));
    }
    @Test void uniqueNamesAreCaseSensitiveAndIncludeDisabledAccounts() throws Exception {
        accounts.create(ADMIN, "first", "Player");
        accounts.create(ADMIN, "second", "player");
        jdbc.update("UPDATE account SET status='DISABLED' WHERE login_name='first'");
        mvc.perform(post("/api/v1/auth/qq/sessions").contentType("application/json").content(registration("Player")))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("GAME_NAME_EXISTS"));
        assertThat(count("account")).isEqualTo(2);
        assertThat(count("auth_session")).isZero();
    }
    @Test void legacyRegistrationIsRestrictedAndRenameRemainsReadableOffline() throws Exception {
        db.createAccount("legacy-id", "legacy", secrets.encode("TEST_PASSWORD"), null);
        var session = auth.login("legacy", "TEST_PASSWORD", "IP");
        String bearer = "Bearer " + session.sessionToken();
        mvc.perform(get("/api/v1/auth/session").header("Authorization", bearer)).andExpect(status().isOk());
        mvc.perform(post("/api/v1/auth/online").header("Authorization", bearer))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("REGISTRATION_REQUIRED"));
        mvc.perform(put("/api/v1/auth/game-name").header("Authorization", bearer).contentType("application/json").content("{\"gameName\":\"玩家\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.gameName").value("玩家"));
        mvc.perform(put("/api/v1/auth/game-name").header("Authorization", bearer).contentType("application/json").content("{\"gameName\":\"其他\"}"))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("GAME_NAME_ALREADY_SET"));
        names.rename(ADMIN, "legacy-id", "管理员改名");
        doThrow(new Problem(403, "QQ_GROUP_NOT_MEMBER", "未入群")).when(groups).require(any(), anyBoolean());
        mvc.perform(get("/api/v1/auth/game-name").header("Authorization", bearer))
                .andExpect(status().isOk()).andExpect(jsonPath("$.gameName").value("管理员改名"));
        assertThat(db.account("legacy-id").loginName()).isEqualTo("legacy");
        assertThat(count("account_presence")).isZero();
    }
    @Test void concurrentNameClaimHasOneWinnerAndAuditFailureRollsBackRename() throws Exception {
        try (var pool = Executors.newFixedThreadPool(2)) {
            var start = new java.util.concurrent.CyclicBarrier(2);
            var jobs = java.util.stream.IntStream.range(0, 2).<Callable<Boolean>>mapToObj(i -> () -> {
                start.await();
                try { accounts.create(ADMIN, "claim-" + i, "唯一名称"); return true; }
                catch (Problem error) { assertThat(error.code()).isEqualTo("GAME_NAME_EXISTS"); return false; }
            }).toList();
            int winners = 0;
            for (var result : pool.invokeAll(jobs)) if (result.get()) winners++;
            assertThat(winners).isEqualTo(1);
        }
        String id = db.gameNameOwner("唯一名称");
        var audit = mock(AdminMapper.class);
        doThrow(new org.springframework.dao.DataAccessResourceFailureException("TEST_FAILURE")).when(audit).audit(any(), any(), any());
        var service = new GameNameService(db, audit, auth, admins, groups, tx);
        assertThatThrownBy(() -> service.rename(ADMIN, id, "更名")).isInstanceOf(org.springframework.dao.DataAccessException.class);
        assertThat(db.account(id).gameName()).isEqualTo("唯一名称");
    }
    private String registration(String name) {
        return json.writeValueAsString(Map.of("machineId", MACHINE, "qqSessionCookie", COOKIE, "gameName", name));
    }
    private int count(String table) { return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class); }
}
