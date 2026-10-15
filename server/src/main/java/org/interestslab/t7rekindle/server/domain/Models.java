package org.interestslab.t7rekindle.server.domain;

import java.time.Instant;
import org.apache.ibatis.annotations.AutomapConstructor;

public final class Models {
    private Models() { }

    public record AccountStats(long total, long enabled, long online) { }

    public record Account(String accountId, String loginName, String passwordHash,
                          String status, long authEpoch, Instant createdAt,
                          String qqNumber, String nickname, String avatarUrl, String gameName) {
        public Account(String accountId, String loginName, String passwordHash, String status, long authEpoch, Instant createdAt,
                       String qqNumber, String nickname, String avatarUrl) {
            this(accountId, loginName, passwordHash, status, authEpoch, createdAt, qqNumber, nickname, avatarUrl, null);
        }
        @AutomapConstructor public Account { }
        public Account(String accountId, String loginName, String passwordHash, String status, long authEpoch, Instant createdAt) {
            this(accountId, loginName, passwordHash, status, authEpoch, createdAt, null, null, null);
        }
    }
    public record Session(String sessionId, String accountId, String tokenHash, long authEpoch,
                          Instant createdAt, Instant expiresAt, Instant revokedAt, String revokeReason, String machineId) {
        @AutomapConstructor public Session { }
        public Session(String sessionId, String accountId, String tokenHash, long authEpoch,
                       Instant createdAt, Instant expiresAt, Instant revokedAt, String revokeReason) {
            this(sessionId, accountId, tokenHash, authEpoch, createdAt, expiresAt, revokedAt, revokeReason, null);
        }
    }
    public record Presence(String accountId, String sessionId, String connectionId, Instant leaseExpiresAt) { }
    public record Admin(int adminId, String loginName, String passwordHash,
                        boolean mustChangePassword, long authEpoch, Instant createdAt) { }
    public record AdminIdentity(long authEpoch, boolean mustChangePassword) { }
    public record Identity(String accountId, String sessionId, String loginName, Instant expiresAt,
                           String machineId, String qqNumber, String nickname, String avatarUrl, String gameName) {
        public Identity(String accountId, String sessionId, String loginName, Instant expiresAt,
                        String machineId, String qqNumber, String nickname, String avatarUrl) {
            this(accountId, sessionId, loginName, expiresAt, machineId, qqNumber, nickname, avatarUrl, null);
        }
        public Identity(String accountId, String sessionId, String loginName, Instant expiresAt) {
            this(accountId, sessionId, loginName, expiresAt, null, null, null, null);
        }
    }
    public record SessionCredentials(String sessionId, String sessionToken, Instant expiresAt) { }
    public record QqProfile(String qqNumber, String nickname, String avatarUrl) { }
    public record QqSessionCredentials(String sessionId, String sessionToken, Instant expiresAt,
            String accountId, String loginName, String machineId, String qqNumber, String nickname, String avatarUrl, String gameName) { }
    public record GameName(String gameName) { }
    public record KickEvent(String accountId, String sessionId, String connectionId, String reason, String groupNumber) {
        public KickEvent(String accountId, String sessionId, String connectionId, String reason) {
            this(accountId, sessionId, connectionId, reason, null);
        }
    }
    public record OnlineConnection(String connectionId, Instant leaseExpiresAt, int heartbeatIntervalSeconds) { }
    public record AccountView(String accountId, String loginName, String status, Instant createdAt,
                              String qqNumber, String nickname, Instant lastHeartbeatAt, boolean online,
                              Instant groupCheckedAt, String groupCheckResult, String gameName) {
        public AccountView(String accountId, String loginName, String status, Instant createdAt,
                           String qqNumber, String nickname, Instant lastHeartbeatAt, boolean online,
                           Instant groupCheckedAt, String groupCheckResult) {
            this(accountId, loginName, status, createdAt, qqNumber, nickname, lastHeartbeatAt, online, groupCheckedAt, groupCheckResult, null);
        }
        @AutomapConstructor public AccountView { }
        public AccountView(String accountId, String loginName, String status, Instant createdAt) {
            this(accountId, loginName, status, createdAt, null, null);
        }
        public AccountView(String accountId, String loginName, String status, Instant createdAt, String qqNumber, String nickname) {
            this(accountId, loginName, status, createdAt, qqNumber, nickname, null, false, null, null);
        }
    }
    public record SessionView(String sessionId, Instant createdAt, Instant expiresAt,
                              String credentialState, boolean online, Instant leaseExpiresAt) { }
    public record Audit(long auditId, String actor, String action, String target, Instant createdAt) { }
}
