package org.interestslab.t7rekindle.server.service;

import java.nio.charset.Charset;
import java.time.Instant;
import java.util.List;
import java.util.Objects;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.interestslab.t7rekindle.server.domain.Models.*;
import org.interestslab.t7rekindle.server.domain.TitleModels.*;
import org.interestslab.t7rekindle.server.domain.Problem;
import org.interestslab.t7rekindle.server.persistence.*;
import static org.interestslab.t7rekindle.server.domain.TitleModels.*;

@Service
public class TitleService {
    private static final Instant START = Instant.parse("2026-09-30T16:00:00Z");
    private static final Instant END = Instant.parse("2026-10-31T16:00:00Z");
    private static final Charset GBK = Charset.forName("GBK");
    private final TitleMapper db;
    private final AuthMapper accounts;
    private final AdminMapper audit;
    private final AuthService auth;
    private final AdminService admins;
    private final TransactionTemplate tx;

    public TitleService(TitleMapper db, AuthMapper accounts, AdminMapper audit, AuthService auth,
                        AdminService admins, TransactionTemplate tx) {
        this.db = db; this.accounts = accounts; this.audit = audit;
        this.auth = auth; this.admins = admins; this.tx = tx;
    }
    // Title writes lock admin (when applicable), catalogue, account, session, presence, then title rows.
    void lockCatalogue() { db.lockCatalogue(); }

    public List<Definition> definitions() { return db.definitions(); }
    public List<Recipient> recipients(int id, String prefix, int page) {
        definition(id);
        return db.recipients(id, Inputs.prefix(prefix), Inputs.offset(page));
    }
    public Snapshot account(String id) { return tx.execute(status -> snapshot(id)); }

    public Snapshot current(Identity identity, String connection) {
        return tx.execute(status -> { requireOwner(identity, connection); return snapshot(identity.accountId()); });
    }
    public Snapshot equip(Identity identity, String connection, Integer id) {
        return tx.execute(status -> {
            lockCatalogue();
            requireOwner(identity, connection);
            if (id != null) {
                Definition title = definition(id);
                Grant grant = db.grantRecord(identity.accountId(), id);
                if (!title.enabled() || grant == null || !grant.state().equals("GRANTED"))
                    throw new Problem(409, "TITLE_NOT_AVAILABLE", "称号尚未获得或已暂停展示");
            }
            db.equip(identity.accountId(), id);
            return snapshot(identity.accountId());
        });
    }
    // Called only by explicit successful online transitions, inside their transaction and locks.
    void rewardOnline(String account, Instant now) {
        if (now.isBefore(START) || !now.isBefore(END) || db.grantRecord(account, 1008) != null) return;
        award(account, definition(1008), "OCTOBER_2026_LOGIN", now);
        audit.audit(account, "TITLE_AUTO_GRANTED", "1008");
    }
    public void grant(AdminIdentity admin, String account, int id) {
        tx.executeWithoutResult(status -> {
            requireManagement(admin, account);
            Definition title = definition(id);
            Grant previous = db.grantRecord(account, id);
            if (previous != null && previous.state().equals("GRANTED")) return;
            award(account, title, "ADMIN", accounts.now());
            audit.audit("admin", "TITLE_GRANTED", account + ":" + id);
        });
    }
    public void revoke(AdminIdentity admin, String account, int id) {
        tx.executeWithoutResult(status -> {
            requireManagement(admin, account);
            definition(id);
            db.revoke(account, id, accounts.now());
            db.clearEquipped(account, id);
            audit.audit("admin", "TITLE_REVOKED", account + ":" + id);
        });
    }
    public int create(AdminIdentity admin, Draft draft) {
        Draft value = validated(draft);
        return tx.execute(status -> {
            admins.requireManagement(admin);
            List<Integer> ids = db.lockCatalogue();
            if (ids.size() >= MAX_TITLES)
                throw new Problem(400, "TITLE_CATALOGUE_FULL", "称号目录最多维护 64 条记录（含已停用称号）");
            long next = Math.max(CUSTOM_ID_START, (long) ids.stream().mapToInt(Integer::intValue).max().orElse(0) + 1);
            if (next > Integer.MAX_VALUE) throw new Problem(400, "TITLE_ID_EXHAUSTED", "称号编号已用尽");
            int id = (int) next;
            db.create(id, value);
            audit.audit("admin", "TITLE_CREATED", Integer.toString(id));
            return id;
        });
    }
    public void configure(AdminIdentity admin, int id, Draft draft) {
        tx.executeWithoutResult(status -> {
            admins.requireManagement(admin);
            lockCatalogue();
            Definition current = definition(id);
            String name = draft.name();
            if (!current.custom()) {
                if (name != null && !name.equals(current.name()))
                    throw new Problem(400, "ORIGINAL_TITLE_NAME", "原游戏称号名称保持不变");
                name = current.name();
            }
            db.configure(id, validated(new Draft(name, draft.description(), draft.sortOrder(), draft.enabled())));
            if (!draft.enabled()) db.clearTitle(id);
            audit.audit("admin", "TITLE_CONFIGURED", Integer.toString(id));
        });
    }
    private static Draft validated(Draft draft) {
        if (!plainText(draft.name(), 31) || draft.name().isBlank())
            throw new Problem(400, "INVALID_TITLE", "名称必填，最多 31 个 GBK 字节（约 15 个汉字），使用纯文本");
        if (!plainText(draft.description(), 64) || draft.sortOrder() < 0 || draft.sortOrder() > 9999)
            throw new Problem(400, "INVALID_TITLE", "介绍最多 64 个 GBK 字节（约 32 个汉字），使用纯文本；排序范围为 0–9999");
        return new Draft(draft.name().strip(), draft.description().strip(), draft.sortOrder(), draft.enabled());
    }
    private static boolean plainText(String text, int bytes) {
        return text != null && GBK.newEncoder().canEncode(text) && text.getBytes(GBK).length <= bytes
                && text.chars().noneMatch(c -> Character.isISOControl(c) || c == '<' || c == '>');
    }
    private void award(String account, Definition title, String source, Instant now) {
        db.grant(account, title.titleId(), source, now);
        if (title.enabled() && db.equipped(account) == null) db.equip(account, title.titleId());
    }
    private void requireManagement(AdminIdentity admin, String account) {
        admins.requireManagement(admin);
        lockCatalogue();
        if (accounts.lockAccount(account) == null) throw Problem.missing();
    }
    private void requireOwner(Identity identity, String connection) {
        Identity current = auth.requireLocked(identity);
        Presence owner = accounts.lockPresence(current.accountId());
        if (owner == null || !owner.leaseExpiresAt().isAfter(accounts.now()))
            throw new Problem(409, "NOT_ONLINE", "在线状态已过期，请重新上线");
        if (!owner.sessionId().equals(current.sessionId()) || !Objects.equals(owner.connectionId(), connection))
            throw new Problem(409, "ONLINE_TAKEN_OVER", "在线资格已由其他连接接管");
    }
    public Definition definition(int id) {
        Definition result = db.definition(id);
        if (result == null) throw Problem.missing();
        return result;
    }
    private Snapshot snapshot(String account) {
        List<Grant> grants = db.grants(account);
        List<Item> items = db.definitions().stream().map(title -> {
            Grant grant = grants.stream().filter(g -> g.titleId() == title.titleId()).findFirst().orElse(null);
            return new Item(title.titleId(), title.name(), title.description(), title.rule(), title.sortOrder(),
                    title.enabled(), grant != null && grant.state().equals("GRANTED"),
                    grant == null ? null : grant.source(), grant == null ? null : grant.grantedAt(), grant == null ? null : grant.revokedAt());
        }).toList();
        Integer equipped = db.equipped(account);
        return new Snapshot(items, items.stream().anyMatch(i -> Objects.equals(i.titleId(), equipped) && i.enabled() && i.owned()) ? equipped : null);
    }
}
