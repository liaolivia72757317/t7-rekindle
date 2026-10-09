package org.t7rekindle.server.service;

import java.util.List;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.t7rekindle.server.domain.Models.*;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.persistence.AdminMapper;
import org.t7rekindle.server.persistence.AuthMapper;

@Service
public class AccountService {
    public record GeneratedPassword(String loginName, String password) { }
    private final AuthMapper db;
    private final AdminMapper audit;
    private final AdminService admins;
    private final Secrets secrets;
    private final TransactionTemplate tx;

    public AccountService(AuthMapper db, AdminMapper audit, AdminService admins, Secrets secrets, TransactionTemplate tx) {
        this.db = db;
        this.audit = audit;
        this.admins = admins;
        this.secrets = secrets;
        this.tx = tx;
    }
    public List<AccountView> list(String prefix, int page) { return db.accounts(Inputs.prefix(prefix), Inputs.offset(page)); }
    public AccountView account(String id) {
        Account account = db.account(id);
        if (account == null) throw Problem.missing();
        return new AccountView(account.accountId(), account.loginName(), account.status(), account.createdAt());
    }
    public List<SessionView> sessions(String id, int page) { return db.sessions(id, Inputs.offset(page)); }
    public List<Audit> audits(int page) { return audit.audits(Inputs.offset(page)); }

    public GeneratedPassword create(AdminIdentity admin, String name) {
        Inputs.loginName(name);
        String password = secrets.randomToken();
        try {
            return tx.execute(status -> {
                admins.requireManagement(admin);
                String id = UUID.randomUUID().toString();
                db.createAccount(id, name, secrets.encode(password));
                audit.audit("admin", "ACCOUNT_CREATED", id);
                return new GeneratedPassword(name, password);
            });
        } catch (DuplicateKeyException error) {
            throw new Problem(409, "LOGIN_NAME_EXISTS", "登录名已存在");
        }
    }
    public GeneratedPassword resetPassword(AdminIdentity admin, String id) {
        return tx.execute(status -> {
            admins.requireManagement(admin);
            Account account = requireAccount(id);
            String password = secrets.randomToken();
            db.resetPassword(id, secrets.encode(password));
            db.clearPresence(id);
            audit.audit("admin", "ACCOUNT_PASSWORD_RESET", id);
            return new GeneratedPassword(account.loginName(), password);
        });
    }
    public void setEnabled(AdminIdentity admin, String id, boolean enabled) {
        tx.executeWithoutResult(status -> {
            admins.requireManagement(admin);
            requireAccount(id);
            db.setStatus(id, enabled ? "ACTIVE" : "DISABLED");
            db.clearPresence(id);
            audit.audit("admin", enabled ? "ACCOUNT_ENABLED" : "ACCOUNT_DISABLED", id);
        });
    }
    public void revokeAll(AdminIdentity admin, String id) {
        tx.executeWithoutResult(status -> {
            admins.requireManagement(admin);
            requireAccount(id);
            db.bumpEpoch(id);
            db.clearPresence(id);
            audit.audit("admin", "ALL_SESSIONS_REVOKED", id);
        });
    }
    public void revoke(AdminIdentity admin, String id, String sessionId) {
        tx.executeWithoutResult(status -> {
            admins.requireManagement(admin);
            requireAccount(id);
            Session session = db.lockSession(sessionId);
            if (session == null || !session.accountId().equals(id)) throw Problem.missing();
            db.revokeSession(sessionId, "ADMIN_REVOKED");
            db.clearSessionPresence(id, sessionId);
            audit.audit("admin", "SESSION_REVOKED", sessionId);
        });
    }
    private Account requireAccount(String id) {
        Account account = db.lockAccount(id);
        if (account == null) throw Problem.missing();
        return account;
    }
}
