package org.interestslab.t7rekindle.server.domain;

import java.time.Instant;
import java.util.List;

public final class TitleModels {
    public static final int CUSTOM_ID_START = 1000000;
    public static final int MAX_TITLES = 64;
    private TitleModels() { }
    public record Definition(int titleId, String name, String description, int sortOrder, boolean enabled) {
        public boolean custom() { return titleId >= CUSTOM_ID_START; }
        public String rule() { return ruleFor(titleId); }
        public String gameDescription() { return description + "\n" + rule(); }
    }
    public record Draft(String name, String description, int sortOrder, boolean enabled) { }
    public record Recipient(String accountId, String loginName, String nickname, String state,
                            Instant grantedAt, Instant revokedAt, boolean equipped) {
        public boolean owned() { return "GRANTED".equals(state); }
    }
    public record Grant(int titleId, String state, String source, Instant grantedAt, Instant revokedAt) { }
    public record Item(int titleId, String name, String description, String rule, int sortOrder,
                       boolean enabled, boolean owned, String source, Instant grantedAt, Instant revokedAt) { }
    public record Snapshot(List<Item> titles, Integer equippedTitleId) { }
    public record Equip(Integer titleId) { }
    public static String ruleFor(int id) {
        return id == 1008 ? "获取：2026年10月功能上线后登录；永久。" : "获取：管理员授予；永久。";
    }
}
