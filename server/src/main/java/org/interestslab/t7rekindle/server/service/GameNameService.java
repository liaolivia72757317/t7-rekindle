package org.interestslab.t7rekindle.server.service;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.AdminMapper;
import org.interestslab.t7rekindle.server.persistence.AuthMapper;

@Service
public class GameNameService {
    private final AuthMapper db;
    private final AdminMapper audit;
    private final AuthService auth;
    private final AdminService admins;
    private final QqGroupService groups;
    private final TransactionTemplate tx;

    public GameNameService(AuthMapper db, AdminMapper audit, AuthService auth, AdminService admins,
                           QqGroupService groups, TransactionTemplate tx) {
        this.db = db; this.audit = audit; this.auth = auth; this.admins = admins; this.groups = groups; this.tx = tx;
    }
    public GameName initialize(Identity identity, String value) {
        String name = GameNameRules.normalize(value);
        groups.require(identity.qqNumber(), false);
        try {
            return tx.execute(status -> {
                Identity current = auth.requireLocked(identity);
                if (current.gameName() != null) {
                    if (!current.gameName().equals(name))
                        throw new Problem(409, "GAME_NAME_ALREADY_SET", "游戏名称已设置，请同步当前名称；后续改名请联系管理员");
                    return new GameName(current.gameName());
                }
                db.setGameName(current.accountId(), name);
                audit.audit(current.accountId(), "ACCOUNT_GAME_NAME_SET", current.accountId());
                return new GameName(name);
            });
        } catch (DuplicateKeyException error) { throw GameNameRules.exists(); }
    }
    public void rename(AdminIdentity admin, String id, String value) {
        String name = GameNameRules.normalize(value);
        try {
            tx.executeWithoutResult(status -> {
                admins.requireManagement(admin);
                Account account = db.lockAccount(id);
                if (account == null) throw Problem.missing();
                if (name.equals(account.gameName())) return;
                db.setGameName(id, name);
                audit.audit("admin", "ACCOUNT_GAME_NAME_CHANGED", id);
            });
        } catch (DuplicateKeyException error) { throw GameNameRules.exists(); }
    }
}
