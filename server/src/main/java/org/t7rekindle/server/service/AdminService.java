package org.t7rekindle.server.service;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.t7rekindle.server.domain.Models.*;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.persistence.AdminMapper;

@Service
public class AdminService {
    private final AdminMapper db;
    private final Secrets secrets;
    private final LoginLimiter limiter;
    private final TransactionTemplate tx;

    public AdminService(AdminMapper db, Secrets secrets, LoginLimiter limiter, TransactionTemplate tx) {
        this.db = db;
        this.secrets = secrets;
        this.limiter = limiter;
        this.tx = tx;
    }
    public String initialize() {
        if (db.admin() != null) return null;
        String password = secrets.randomToken();
        String hash = secrets.encode(password);
        try {
            return tx.execute(status -> {
                db.createAdmin(hash);
                db.audit("system", "ADMIN_INITIALIZED", "admin");
                return password;
            });
        } catch (DuplicateKeyException error) {
            if (db.admin() == null) throw error;
            return null;
        }
    }
    public AdminIdentity login(String name, String password, String address) {
        Inputs.loginName(name);
        Inputs.password(password);
        limiter.check("admin", name, address);
        AdminIdentity identity = tx.execute(status -> {
            Admin admin = db.lockAdmin();
            boolean matches = secrets.matches(password, admin == null ? null : admin.passwordHash());
            if (admin == null || !name.equals("admin") || !matches) {
                db.audit("anonymous", "ADMIN_LOGIN_FAILED", name);
                return null;
            }
            db.audit("admin", "ADMIN_LOGIN", "admin");
            return new AdminIdentity(admin.authEpoch(), admin.mustChangePassword());
        });
        if (identity == null) throw Problem.unauthorized();
        return identity;
    }
    public AdminIdentity current(AdminIdentity identity) {
        return validate(db.admin(), identity);
    }
    public void requireManagement(AdminIdentity identity) {
        if (validate(db.lockAdmin(), identity).mustChangePassword()) {
            throw new Problem(403, "PASSWORD_CHANGE_REQUIRED", "请先修改管理员密码");
        }
    }
    public void changePassword(AdminIdentity identity, String oldPassword, String newPassword) {
        Inputs.password(oldPassword);
        Inputs.password(newPassword);
        if (oldPassword.equals(newPassword)) throw new Problem(400, "PASSWORD_UNCHANGED", "新密码应与原密码不同");
        tx.executeWithoutResult(status -> {
            Admin admin = db.lockAdmin();
            validate(admin, identity);
            if (!secrets.matches(oldPassword, admin.passwordHash())) throw Problem.unauthorized();
            db.changePassword(secrets.encode(newPassword), false);
            db.audit("admin", "ADMIN_PASSWORD_CHANGED", "admin");
        });
    }
    public String reset() {
        return tx.execute(status -> {
            if (db.lockAdmin() == null) throw Problem.missing();
            String password = secrets.randomToken();
            db.changePassword(secrets.encode(password), true);
            db.audit("local-console", "ADMIN_PASSWORD_RESET", "admin");
            return password;
        });
    }
    private AdminIdentity validate(Admin admin, AdminIdentity identity) {
        if (admin == null || identity == null || admin.authEpoch() != identity.authEpoch()) {
            throw Problem.unauthorized();
        }
        return new AdminIdentity(admin.authEpoch(), admin.mustChangePassword());
    }
}
