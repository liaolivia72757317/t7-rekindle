package org.interestslab.t7rekindle.server.service;

import java.time.Instant;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.AuthMapper;

@Service
public class PresenceService {
    private final AuthMapper db;
    private final AuthService auth;
    private final TransactionTemplate tx;
    private final SessionEvents events;
    private final QqGroupService groups;
    private final TitleService titles;

    public PresenceService(AuthMapper db, AuthService auth, TransactionTemplate tx, SessionEvents events, QqGroupService groups, TitleService titles) {
        this.db = db;
        this.auth = auth;
        this.tx = tx;
        this.events = events;
        this.groups = groups;
        this.titles = titles;
    }
    public OnlineConnection online(Identity identity) {
        groups.require(identity.qqNumber(), false);
        return tx.execute(status -> {
            titles.lockCatalogue();
            Identity current = auth.requireLocked(identity);
            GameNameRules.requireRegistered(current.gameName());
            Presence previous = db.lockPresence(current.accountId());
            Instant now = db.now();
            var result = save(current, UUID.randomUUID().toString(), now);
            titles.rewardOnline(current.accountId(), now);
            events.kick(previous, "ONLINE_TAKEN_OVER");
            return result;
        });
    }
    public OnlineConnection heartbeat(Identity identity, String connectionId) {
        checkConnection(identity, connectionId);
        groups.require(identity.qqNumber(), true);
        return tx.execute(status -> {
            Identity current = auth.requireLocked(identity);
            GameNameRules.requireRegistered(current.gameName());
            Instant now = db.now();
            requireOwner(current, connectionId, now);
            db.heartbeat(current.accountId(), now);
            return save(current, connectionId, now);
        });
    }
    public void checkConnection(Identity identity, String connectionId) {
        tx.executeWithoutResult(status -> requireOwner(auth.requireLocked(identity), connectionId, db.now()));
    }
    private void requireOwner(Identity identity, String connectionId, Instant now) {
        Presence owner = db.lockPresence(identity.accountId());
        if (owner == null || !owner.leaseExpiresAt().isAfter(now)) {
            throw new Problem(409, "NOT_ONLINE", "在线状态已过期，请重新上线");
        }
        if (!owner.sessionId().equals(identity.sessionId()) || !owner.connectionId().equals(connectionId)) {
            throw new Problem(409, "ONLINE_TAKEN_OVER", "在线资格已由其他连接接管");
        }
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
