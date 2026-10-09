package org.t7rekindle.server;

import java.io.IOException;
import java.net.CookieManager;
import java.net.CookiePolicy;
import java.net.ServerSocket;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.mysql.MySQLContainer;
import org.testcontainers.utility.DockerImageName;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;

class PackagedJarIT {
    private static final Pattern PASSWORD = Pattern.compile("One-time password: ([A-Za-z0-9_-]{43})");
    private static final Pattern CSRF = Pattern.compile("name=\"_csrf\"[^>]*value=\"([^\"]+)\"");
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3))
            .cookieHandler(new CookieManager(null, CookiePolicy.ACCEPT_ALL)).build();
    private String base;
    private MySQLContainer mysql;
    private GenericContainer<?> redis;

    @Test void jarMigratesBootstrapsSurvivesRestartAndRecoversWithoutFrontendRuntime() throws Exception {
        try (var database = new MySQLContainer("mysql:8.4.11").withTmpFs(Map.of("/var/lib/mysql", "rw"));
             var cache = new GenericContainer<>(DockerImageName.parse("redis:8.6.6")).withExposedPorts(6379)) {
            mysql = database;
            redis = cache;
            database.start();
            cache.start();
            int port;
            try (var socket = new ServerSocket(0)) { port = socket.getLocalPort(); }
            base = "http://127.0.0.1:" + port;
            Path migrationLog = Path.of("target/jar-migration.log");
            assertThat(command(migrationLog, "--migrate")).isZero();
            assertThat(command(migrationLog, "--migrate")).isZero();
            Path firstLog = Path.of("target/jar-first-start.log");
            Process server = start(firstLog, "--server.port=" + port);
            String bearer;
            String accountPassword;
            try {
                String initial = awaitPassword(firstLog, server);
                var loginPage = awaitPage("/admin/login", server);
                assertThat(loginPage.statusCode()).isEqualTo(200);
                String cookie = loginPage.headers().firstValue("set-cookie").orElseThrow();
                assertThat(cookie).contains("HttpOnly", "SameSite=Lax", "Path=/admin");
                assertThat(get("/admin/assets/admin.css").body()).contains("--ink: #234759");
                assertThat(post("/admin/login", Map.of("loginName", "admin", "password", initial, "_csrf", csrf(loginPage.body())))
                        .headers().firstValue("location").map(location -> URI.create(location).getPath())).contains("/admin/password");
                assertThat(get("/admin/accounts").statusCode()).isEqualTo(302);
                String csrf = csrf(get("/admin/password").body());
                var changed = post("/admin/password", Map.of("oldPassword", initial, "newPassword", "JAR_TEST_PASSWORD",
                        "confirmation", "JAR_TEST_PASSWORD", "_csrf", csrf));
                assertThat(changed.statusCode()).isEqualTo(302);
                login("JAR_TEST_PASSWORD");
                String accounts = get("/admin/accounts").body();
                assertThat(accounts).contains("创建普通账户");
                var created = post("/admin/accounts", Map.of("loginName", "jar-user", "_csrf", csrf(accounts)));
                String resultPage = get(created.headers().firstValue("location").orElseThrow()).body();
                var secret = Pattern.compile("data-secret[^>]*>([^<]+)</code>").matcher(resultPage);
                assertThat(secret.find()).isTrue();
                accountPassword = secret.group(1);
                var response = api("POST", "/api/v1/auth/sessions", null,
                        "{\"loginName\":\"jar-user\",\"password\":\"" + accountPassword + "\"}");
                assertThat(response.statusCode()).isEqualTo(201);
                bearer = JsonMapper.builder().build().readTree(response.body()).get("sessionToken").asString();
            } finally { stop(server); }

            Path restartLog = Path.of("target/jar-restart.log");
            server = start(restartLog, "--server.port=" + port);
            try {
                awaitPage("/admin/login", server);
                assertThat(get("/admin/accounts").headers().firstValue("location").map(location -> URI.create(location).getPath())).contains("/admin/login");
                login("JAR_TEST_PASSWORD");
                assertThat(Files.readString(restartLog)).doesNotContain("One-time password:");
                Path recoveryLog = Path.of("target/jar-recovery.log");
                assertThat(command(recoveryLog, "--reset-admin")).isNotZero();
                assertThat(get("/admin/accounts").statusCode()).isEqualTo(200);
                assertThat(command(recoveryLog, "--reset-admin", "--confirm-reset-admin")).isZero();
                assertThat(get("/admin/accounts").statusCode()).isEqualTo(302);
                var recovered = PASSWORD.matcher(Files.readString(recoveryLog));
                assertThat(recovered.find()).isTrue();
                login(recovered.group(1));
                assertThat(get("/admin/accounts").headers().firstValue("location").map(location -> URI.create(location).getPath())).contains("/admin/password");
                var online = api("POST", "/api/v1/auth/online", bearer, "");
                assertThat(online.statusCode()).isEqualTo(200);
                String connection = JsonMapper.builder().build().readTree(online.body()).get("connectionId").asString();
                cache.stop();
                assertThat(api("GET", "/api/v1/auth/session", bearer, "").statusCode()).isEqualTo(200);
                assertThat(api("POST", "/api/v1/auth/online/" + connection + "/heartbeat", bearer, "").statusCode()).isEqualTo(200);
                assertThat(api("POST", "/api/v1/auth/sessions", null,
                        "{\"loginName\":\"jar-user\",\"password\":\"" + accountPassword + "\"}").statusCode()).isEqualTo(503);
                database.stop();
                assertThat(api("GET", "/api/v1/auth/session", bearer, "").statusCode()).isEqualTo(503);
            } finally { stop(server); }
        }
    }

    private void login(String password) throws Exception {
        String csrf = csrf(get("/admin/login").body());
        assertThat(post("/admin/login", Map.of("loginName", "admin", "password", password, "_csrf", csrf)).statusCode()).isEqualTo(302);
    }
    private String csrf(String html) {
        var matcher = CSRF.matcher(html);
        assertThat(matcher.find()).as("Rendered form contains a CSRF token").isTrue();
        return matcher.group(1);
    }
    private HttpResponse<String> get(String path) throws Exception {
        return http.send(HttpRequest.newBuilder(URI.create(base).resolve(path)).timeout(Duration.ofSeconds(10)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
    }
    private HttpResponse<String> post(String path, Map<String, String> fields) throws Exception {
        String data = fields.entrySet().stream().map(entry -> URLEncoder.encode(entry.getKey(), StandardCharsets.UTF_8)
                + "=" + URLEncoder.encode(entry.getValue(), StandardCharsets.UTF_8)).collect(Collectors.joining("&"));
        return http.send(HttpRequest.newBuilder(URI.create(base).resolve(path)).timeout(Duration.ofSeconds(15))
                .header("Content-Type", "application/x-www-form-urlencoded").POST(HttpRequest.BodyPublishers.ofString(data)).build(),
                HttpResponse.BodyHandlers.ofString());
    }
    private HttpResponse<String> api(String method, String path, String token, String body) throws Exception {
        var request = HttpRequest.newBuilder(URI.create(base).resolve(path)).timeout(Duration.ofSeconds(15))
                .header("Content-Type", "application/json").method(method, HttpRequest.BodyPublishers.ofString(body));
        if (token != null) request.header("Authorization", "Bearer " + token);
        return http.send(request.build(), HttpResponse.BodyHandlers.ofString());
    }
    private Process start(Path log, String... args) throws IOException {
        var command = new ArrayList<>(List.of(Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-jar", Path.of("target/rekindle-server-0.1.0-SNAPSHOT.jar").toAbsolutePath().toString(),
                "--server.servlet.session.cookie.secure=false"));
        command.addAll(List.of(args));
        var builder = new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile());
        builder.environment().putAll(Map.of("DB_URL", mysql.getJdbcUrl(), "DB_USER", mysql.getUsername(),
                "DB_PASSWORD", mysql.getPassword(), "MIGRATION_USER", mysql.getUsername(), "MIGRATION_PASSWORD", mysql.getPassword(),
                "REDIS_HOST", redis.getHost(), "REDIS_PORT", redis.getMappedPort(6379).toString()));
        return builder.start();
    }
    private int command(Path log, String... args) throws Exception {
        Process process = start(log, args);
        try {
            assertThat(process.waitFor(120, TimeUnit.SECONDS)).as("Jar command completes").isTrue();
            return process.exitValue();
        } finally { stop(process); }
    }
    private String awaitPassword(Path log, Process process) throws Exception {
        for (int i = 0; i < 600; i++) {
            assertThat(process.isAlive()).as("Server startup succeeds").isTrue();
            var matcher = PASSWORD.matcher(Files.readString(log));
            if (matcher.find()) return matcher.group(1);
            Thread.sleep(200);
        }
        throw new AssertionError("Administrator was not initialized within 120 seconds");
    }
    private HttpResponse<String> awaitPage(String path, Process process) throws Exception {
        IOException lastError = null;
        for (int i = 0; i < 600; i++) {
            assertThat(process.isAlive()).isTrue();
            try { return get(path); }
            catch (IOException error) { lastError = error; Thread.sleep(200); }
        }
        throw new AssertionError("HTTP server did not become ready", lastError);
    }
    private void stop(Process process) throws InterruptedException {
        process.destroy();
        if (!process.waitFor(10, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            process.waitFor(10, TimeUnit.SECONDS);
        }
    }
}
