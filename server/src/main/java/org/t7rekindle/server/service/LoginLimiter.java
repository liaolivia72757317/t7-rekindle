package org.t7rekindle.server.service;

import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;
import org.t7rekindle.server.domain.Problem;

@Service
public class LoginLimiter {
    private static final DefaultRedisScript<Long> SCRIPT = new DefaultRedisScript<>("""
        local account = redis.call('INCR', KEYS[1])
        if account == 1 then redis.call('EXPIRE', KEYS[1], 300) end
        local ip = redis.call('INCR', KEYS[2])
        if ip == 1 then redis.call('EXPIRE', KEYS[2], 60) end
        if account > tonumber(ARGV[1]) or ip > tonumber(ARGV[2]) then return 0 end
        return 1
        """, Long.class);
    private final StringRedisTemplate redis;
    private final int accountAttempts;
    private final int ipAttempts;

    public LoginLimiter(StringRedisTemplate redis,
            @Value("${rekindle.login-limit.account-attempts:10}") int accountAttempts,
            @Value("${rekindle.login-limit.ip-attempts:30}") int ipAttempts) {
        if (accountAttempts < 1 || ipAttempts < 1) throw new IllegalArgumentException("Login limits must be positive");
        this.redis = redis;
        this.accountAttempts = accountAttempts;
        this.ipAttempts = ipAttempts;
    }
    public void check(String realm, String name, String address) {
        Long allowed;
        try {
            allowed = redis.execute(SCRIPT, List.of("login:" + realm + ":account:" + Secrets.hash(name),
                    "login:" + realm + ":ip:" + Secrets.hash(address)),
                    Integer.toString(accountAttempts), Integer.toString(ipAttempts));
        } catch (DataAccessException error) {
            throw Problem.unavailable();
        }
        if (allowed == null) throw Problem.unavailable();
        if (allowed != 1) throw new Problem(429, "LOGIN_RATE_LIMITED", "登录过于频繁，请在 5 分钟后重试");
    }
}
