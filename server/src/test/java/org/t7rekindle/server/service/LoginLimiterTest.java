package org.t7rekindle.server.service;

import org.junit.jupiter.api.Test;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.RedisScript;
import org.t7rekindle.server.domain.Problem;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class LoginLimiterTest {
    @Test void failClosedAndEnforceLimits() {
        var redis = mock(StringRedisTemplate.class);
        var limiter = new LoginLimiter(redis, 10, 30);
        when(redis.execute(org.mockito.ArgumentMatchers.<RedisScript<Long>>any(), anyList(), any(Object[].class))).thenReturn(1L);
        limiter.check("account", "sample", "IP");
        when(redis.execute(org.mockito.ArgumentMatchers.<RedisScript<Long>>any(), anyList(), any(Object[].class))).thenReturn(0L);
        assertStatus(limiter, 429);
        when(redis.execute(org.mockito.ArgumentMatchers.<RedisScript<Long>>any(), anyList(), any(Object[].class))).thenReturn(null);
        assertStatus(limiter, 503);
        when(redis.execute(org.mockito.ArgumentMatchers.<RedisScript<Long>>any(), anyList(), any(Object[].class)))
                .thenThrow(new RedisConnectionFailureException("offline"));
        assertStatus(limiter, 503);
        assertThatThrownBy(() -> new LoginLimiter(redis, 0, 30)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new LoginLimiter(redis, 10, 0)).isInstanceOf(IllegalArgumentException.class);
    }
    private void assertStatus(LoginLimiter limiter, int status) {
        assertThatThrownBy(() -> limiter.check("account", "sample", "IP")).isInstanceOfSatisfying(Problem.class,
                error -> assertThat(error.status()).isEqualTo(status));
    }
}
