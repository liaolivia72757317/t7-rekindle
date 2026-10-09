package org.t7rekindle.server.service;

import java.time.Instant;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.t7rekindle.server.domain.Models.*;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.persistence.AuthMapper;

@Service
public class PresenceService {
    private final AuthMapper db;
    private final AuthService auth;
    private final TransactionTemplate tx;

    public PresenceService(AuthMapper db, AuthService auth, TransactionTemplate tx) {
        this.db = db;
        this.auth = auth;
        this.tx = tx;
    }
    public OnlineConnection online(Identity identity) {
        return tx.execute(status -> save(auth.requireLocked(identity), UUID.randomUUID().toString(), db.now()));
    }
    public OnlineConnection heartbeat(Identity identity, String connectionId) {
        return tx.execute(status -> {
            Identity current = auth.requireLocked(identity);
            Presence owner = db.lockPresence(current.accountId());
            Instant now = db.now();
            if (owner == null || !owner.leaseExpiresAt().isAfter(now)) {
                throw new Problem(409, "NOT_ONLINE", "在线状态已过期，请重新上线");
            }
            if (!owner.sessionId().equals(current.sessionId()) || !owner.connectionId().equals(connectionId)) {
                throw new Problem(409, "ONLINE_TAKEN_OVER", "在线资格已由其他连接接管");
            }
            return save(current, connectionId, now);
        });
    }
    public void offline(Identity identity, String connectionId) {
        tx.executeWithoutResult(status -> {
            auth.requireLocked(identity);
            db.clearConnection(identity.accountId(), identity.sessionId(), connectionId);
        });
    }
    private OnlineConnection save(Identity identity, String connectionId, Instant now) {
        Instant leaseEnd = now.plusSeconds(45);
        Instant deadline = leaseEnd.isBefore(identity.expiresAt()) ? leaseEnd : identity.expiresAt();
        db.savePresence(new Presence(identity.accountId(), identity.sessionId(), connectionId, deadline));
        return new OnlineConnection(connectionId, deadline, 15);
    }
}
