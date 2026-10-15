package org.interestslab.t7rekindle.server.persistence;

import java.time.Instant;
import java.util.List;
import org.apache.ibatis.annotations.*;
import org.interestslab.t7rekindle.server.domain.Models.*;

@Mapper
public interface AuthMapper {
    @Select("SELECT UTC_TIMESTAMP(3)")
    Instant now();

    @Select("SELECT * FROM account WHERE login_name = #{name} FOR UPDATE")
    Account lockByName(String name);

    @Select("SELECT * FROM account WHERE login_name = #{name}")
    Account accountByName(String name);

    @Select("SELECT * FROM account WHERE account_id = #{id} FOR UPDATE")
    Account lockAccount(String id);

    @Select("SELECT * FROM account WHERE account_id = #{id}")
    Account account(String id);

    @Select("SELECT * FROM auth_session WHERE token_hash = #{hash}")
    Session sessionByHash(String hash);

    @Select("SELECT * FROM auth_session WHERE session_id = #{id} FOR UPDATE")
    Session lockSession(String id);

    @Insert("INSERT INTO account(account_id,login_name,password_hash,game_name) VALUES(#{id},#{name},#{hash},#{gameName})")
    void createAccount(String id, String name, String hash, String gameName);

    @Insert("""
        INSERT INTO account(account_id,login_name,qq_number,nickname,avatar_url,game_name)
        VALUES(#{id},#{profile.qqNumber},#{profile.qqNumber},#{profile.nickname},#{profile.avatarUrl},#{gameName})
        """)
    void createQqAccount(String id, QqProfile profile, String gameName);

    @Update("UPDATE account SET game_name=#{gameName} WHERE account_id=#{id}")
    void setGameName(String id, String gameName);

    @Select("SELECT account_id FROM account WHERE game_name=#{gameName}")
    String gameNameOwner(String gameName);

    @Update("UPDATE account SET nickname=#{profile.nickname},avatar_url=#{profile.avatarUrl} WHERE account_id=#{id}")
    void updateQqProfile(String id, QqProfile profile);

    @Select("SELECT * FROM auth_session WHERE account_id=#{id} AND revoked_at IS NULL ORDER BY session_id FOR UPDATE")
    List<Session> lockSessions(String id);

    @Insert("""
        INSERT INTO auth_session(session_id,account_id,token_hash,auth_epoch,created_at,expires_at,machine_id)
        VALUES(#{sessionId},#{accountId},#{tokenHash},#{authEpoch},#{createdAt},#{expiresAt},#{machineId})
        """)
    void createSession(Session session);

    @Update("UPDATE auth_session SET revoked_at=UTC_TIMESTAMP(3),revoke_reason=#{reason} WHERE session_id=#{id} AND revoked_at IS NULL")
    void revokeSession(String id, String reason);

    @Update("UPDATE account SET auth_epoch=auth_epoch+1 WHERE account_id=#{id}")
    void bumpEpoch(String id);

    @Update("UPDATE account SET password_hash=#{hash},auth_epoch=auth_epoch+1 WHERE account_id=#{id}")
    void resetPassword(String id, String hash);

    @Update("UPDATE account SET status=#{status},auth_epoch=auth_epoch+1 WHERE account_id=#{id}")
    void setStatus(String id, String status);

    @Select("SELECT * FROM account_presence WHERE account_id=#{id} FOR UPDATE")
    Presence lockPresence(String id);

    @Insert("""
        INSERT INTO account_presence(account_id,session_id,connection_id,lease_expires_at)
        VALUES(#{accountId},#{sessionId},#{connectionId},#{leaseExpiresAt}) AS incoming
        ON DUPLICATE KEY UPDATE session_id=incoming.session_id,connection_id=incoming.connection_id,
        lease_expires_at=incoming.lease_expires_at
        """)
    void savePresence(Presence presence);

    @Delete("DELETE FROM account_presence WHERE account_id=#{id}")
    void clearPresence(String id);

    @Delete("DELETE FROM account_presence WHERE account_id=#{accountId} AND session_id=#{sessionId}")
    void clearSessionPresence(String accountId, String sessionId);

    @Delete("DELETE FROM account_presence WHERE account_id=#{accountId} AND session_id=#{sessionId} AND connection_id=#{connectionId}")
    void clearConnection(String accountId, String sessionId, String connectionId);

    @Update("UPDATE account SET last_heartbeat_at=#{now} WHERE account_id=#{accountId}")
    void heartbeat(String accountId, Instant now);

    @Select("""
        SELECT a.account_id,a.login_name,a.status,a.created_at,a.qq_number,a.nickname,a.last_heartbeat_at,a.game_name,
          (p.session_id IS NOT NULL AND p.lease_expires_at > UTC_TIMESTAMP(3) AND a.status='ACTIVE' AND a.game_name IS NOT NULL
            AND s.expires_at > UTC_TIMESTAMP(3) AND s.revoked_at IS NULL AND s.auth_epoch=a.auth_epoch) AS online,
          c.checked_at AS group_checked_at,c.result AS group_check_result
        FROM account a
        LEFT JOIN account_presence p ON p.account_id=a.account_id
        LEFT JOIN auth_session s ON s.session_id=p.session_id
        LEFT JOIN qq_group_config g ON g.config_id=1
        LEFT JOIN qq_group_check c ON c.qq_number=a.qq_number AND g.enabled=TRUE
          AND c.group_number=g.group_number AND c.policy_version=g.policy_version
        WHERE a.login_name LIKE CONCAT(#{prefix},'%') ORDER BY a.login_name LIMIT 50 OFFSET #{offset}
        """)
    List<AccountView> accounts(String prefix, int offset);

    @Select("""
        SELECT COUNT(*) AS total,
          COALESCE(SUM(a.status='ACTIVE'), 0) AS enabled,
          COALESCE(SUM(CASE WHEN p.session_id IS NOT NULL AND p.lease_expires_at > UTC_TIMESTAMP(3)
            AND a.status='ACTIVE' AND a.game_name IS NOT NULL AND s.expires_at > UTC_TIMESTAMP(3) AND s.revoked_at IS NULL
            AND s.auth_epoch=a.auth_epoch THEN 1 ELSE 0 END), 0) AS online
        FROM account a
        LEFT JOIN account_presence p ON p.account_id=a.account_id
        LEFT JOIN auth_session s ON s.session_id=p.session_id
        """)
    AccountStats accountStats();

    @Select("""
        SELECT s.session_id,s.created_at,s.expires_at,
          CASE WHEN s.revoked_at IS NOT NULL OR s.auth_epoch != a.auth_epoch OR a.status != 'ACTIVE' THEN 'REVOKED'
               WHEN s.expires_at <= UTC_TIMESTAMP(3) THEN 'EXPIRED' ELSE 'VALID' END AS credential_state,
          (p.session_id IS NOT NULL AND p.lease_expires_at > UTC_TIMESTAMP(3)
            AND s.expires_at > UTC_TIMESTAMP(3) AND s.revoked_at IS NULL
            AND s.auth_epoch = a.auth_epoch AND a.status = 'ACTIVE' AND a.game_name IS NOT NULL) AS online,
          p.lease_expires_at
        FROM auth_session s JOIN account a ON a.account_id=s.account_id
        LEFT JOIN account_presence p ON p.account_id=s.account_id AND p.session_id=s.session_id
        WHERE s.account_id=#{id} ORDER BY s.created_at DESC,s.session_id LIMIT 50 OFFSET #{offset}
        """)
    List<SessionView> sessions(String id, int offset);
}
