package org.t7rekindle.server.domain;

import java.time.Instant;

public final class Models {
    private Models() { }

    public record Account(String accountId, String loginName, String passwordHash,
                          String status, long authEpoch, Instant createdAt) { }
    public record Session(String sessionId, String accountId, String tokenHash, long authEpoch,
                          Instant createdAt, Instant expiresAt, Instant revokedAt, String revokeReason) { }
    public record Presence(String accountId, String sessionId, String connectionId, Instant leaseExpiresAt) { }
    public record Admin(int adminId, String loginName, String passwordHash,
                        boolean mustChangePassword, long authEpoch, Instant createdAt) { }
    public record AdminIdentity(long authEpoch, boolean mustChangePassword) { }
    public record Identity(String accountId, String sessionId, String loginName, Instant expiresAt) { }
    public record SessionCredentials(String sessionId, String sessionToken, Instant expiresAt) { }
    public record OnlineConnection(String connectionId, Instant leaseExpiresAt, int heartbeatIntervalSeconds) { }
    public record AccountView(String accountId, String loginName, String status, Instant createdAt) { }
    public record SessionView(String sessionId, Instant createdAt, Instant expiresAt,
                              String credentialState, boolean online, Instant leaseExpiresAt) { }
    public record Audit(long auditId, String actor, String action, String target, Instant createdAt) { }
}
