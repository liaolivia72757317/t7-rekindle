package org.t7rekindle.server.web;

import java.time.Clock;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import jakarta.servlet.http.HttpSession;
import org.springframework.stereotype.Component;
import org.t7rekindle.server.service.AccountService.GeneratedPassword;

@Component
public class SecretResults {
    private static final String ATTRIBUTE = "oneTimeSecrets";
    private final Clock clock;
    public SecretResults(Clock clock) { this.clock = clock; }

    public String store(HttpSession session, GeneratedPassword value) {
        return vault(session).put(value, clock.instant());
    }
    public GeneratedPassword consume(HttpSession session, String id) {
        return vault(session).take(id, clock.instant());
    }
    private Vault vault(HttpSession session) {
        synchronized (session) {
            Object value = session.getAttribute(ATTRIBUTE);
            if (value instanceof Vault existing) return existing;
            var created = new Vault();
            session.setAttribute(ATTRIBUTE, created);
            return created;
        }
    }
    private record Entry(GeneratedPassword value, Instant expiresAt) { }
    private static final class Vault {
        private final Map<String, Entry> entries = new LinkedHashMap<>();
        synchronized String put(GeneratedPassword value, Instant now) {
            entries.values().removeIf(entry -> !entry.expiresAt().isAfter(now));
            if (entries.size() >= 10) entries.remove(entries.keySet().iterator().next());
            String id = UUID.randomUUID().toString();
            entries.put(id, new Entry(value, now.plusSeconds(300)));
            return id;
        }
        synchronized GeneratedPassword take(String id, Instant now) {
            Entry entry = entries.remove(id);
            return entry != null && entry.expiresAt().isAfter(now) ? entry.value() : null;
        }
    }
}
