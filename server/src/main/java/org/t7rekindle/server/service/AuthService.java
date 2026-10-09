package org.t7rekindle.server.service;

import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.t7rekindle.server.domain.Models.*;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.persistence.AdminMapper;
import org.t7rekindle.server.persistence.AuthMapper;

@Service
public class AuthService {
    private final AuthMapper db;
    private final AdminMapper audit;
    private final Secrets secrets;
    private final LoginLimiter limiter;
    private final TransactionTemplate tx;

    public AuthService(AuthMapper db, AdminMapper audit, Secrets secrets, LoginLimiter limiter, TransactionTemplate tx) {
        this.db = db;
        this.audit = audit;
        this.secrets = secrets;
        this.limiter = limiter;
        this.tx = tx;
    }

    public SessionCredentials login(String name, String password, String address) {
        Inputs.loginName(name);
        Inputs.password(password);
        limiter.check("account", name, address);
        SessionCredentials result = tx.execute(status -> {
            Account account = db.lockByName(name);
            boolean passwordMatches = secrets.matches(password, account == null ? null : account.passwordHash());
            if (account == null || !passwordMatches || !account.status().equals("ACTIVE")) {
                audit.audit("anonymous", "ACCOUNT_LOGIN_FAILED", name);
                return null;
            }
            Instant now = db.now();
            String token = secrets.randomToken();
            var session = new Session(UUID.randomUUID().toString(), account.accountId(), Secrets.hash(token),
                    account.authEpoch(), now, now.plus(Duration.ofDays(30)), null, null);
            db.createSession(session);
            audit.audit(account.accountId(), "ACCOUNT_LOGIN", session.sessionId());
            return new SessionCredentials(session.sessionId(), token, session.expiresAt());
        });
        if (result == null) throw Problem.unauthorized();
        return result;
    }

    public Identity authenticate(String token) {
        if (token == null || !token.matches("[A-Za-z0-9_-]{43}")) throw Problem.unauthorized();
        Session session = db.sessionByHash(Secrets.hash(token));
        if (session == null) throw Problem.unauthorized();
        return validate(db.account(session.accountId()), session, db.now());
    }

    public Identity requireLocked(Identity identity) {
        Account account = db.lockAccount(identity.accountId());
        Session session = db.lockSession(identity.sessionId());
        return validate(account, session, db.now());
    }

    public void logout(Identity identity) {
        tx.executeWithoutResult(status -> {
            requireLocked(identity);
            db.revokeSession(identity.sessionId(), "LOGOUT");
            db.clearSessionPresence(identity.accountId(), identity.sessionId());
            audit.audit(identity.accountId(), "ACCOUNT_LOGOUT", identity.sessionId());
        });
    }

    private Identity validate(Account account, Session session, Instant now) {
        if (account == null || session == null || !account.accountId().equals(session.accountId())
                || !account.status().equals("ACTIVE") || session.revokedAt() != null
                || account.authEpoch() != session.authEpoch() || !session.expiresAt().isAfter(now)) {
            throw Problem.unauthorized();
        }
        return new Identity(account.accountId(), session.sessionId(), account.loginName(), session.expiresAt());
    }
}
