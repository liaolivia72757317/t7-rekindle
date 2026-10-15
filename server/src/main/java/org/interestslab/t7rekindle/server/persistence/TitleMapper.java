package org.interestslab.t7rekindle.server.persistence;

import java.time.Instant;
import java.util.List;
import org.apache.ibatis.annotations.*;
import org.interestslab.t7rekindle.server.domain.TitleModels.*;

@Mapper
public interface TitleMapper {
    @Select("SELECT title_id FROM player_title ORDER BY title_id FOR UPDATE")
    List<Integer> lockCatalogue();
    @Select("SELECT * FROM player_title ORDER BY sort_order,title_id")
    List<Definition> definitions();
    @Select("SELECT * FROM player_title WHERE title_id=#{id}")
    Definition definition(int id);
    @Insert("INSERT INTO player_title(title_id,name,description,sort_order,enabled) VALUES(#{id},#{draft.name},#{draft.description},#{draft.sortOrder},#{draft.enabled})")
    void create(int id, Draft draft);
    @Select("""
            SELECT a.account_id,a.login_name,a.nickname,t.state,t.granted_at,t.revoked_at,
              COALESCE(s.title_id=#{id},FALSE) AS equipped
            FROM account a
            LEFT JOIN account_title t ON t.account_id=a.account_id AND t.title_id=#{id}
            LEFT JOIN account_title_selection s ON s.account_id=a.account_id
            WHERE a.login_name LIKE CONCAT(#{prefix},'%') ORDER BY a.login_name LIMIT 50 OFFSET #{offset}
            """)
    List<Recipient> recipients(int id, String prefix, int offset);
    @Select("SELECT title_id,state,source,granted_at,revoked_at FROM account_title WHERE account_id=#{account} AND title_id=#{id}")
    Grant grantRecord(String account, int id);
    @Select("SELECT title_id,state,source,granted_at,revoked_at FROM account_title WHERE account_id=#{account}")
    List<Grant> grants(String account);
    @Select("SELECT title_id FROM account_title_selection WHERE account_id=#{account}")
    Integer equipped(String account);
    @Insert("""
            INSERT INTO account_title(account_id,title_id,state,source,granted_at)
            VALUES(#{account},#{id},'GRANTED',#{source},#{now})
            ON DUPLICATE KEY UPDATE state='GRANTED',source=#{source},granted_at=#{now},revoked_at=NULL
            """)
    void grant(String account, int id, String source, Instant now);
    @Insert("""
            INSERT INTO account_title(account_id,title_id,state,source,granted_at,revoked_at)
            VALUES(#{account},#{id},'REVOKED','ADMIN',#{now},#{now})
            ON DUPLICATE KEY UPDATE state='REVOKED',revoked_at=#{now}
            """)
    void revoke(String account, int id, Instant now);
    @Insert("""
            INSERT INTO account_title_selection(account_id,title_id) VALUES(#{account},#{id})
            ON DUPLICATE KEY UPDATE title_id=#{id}
            """)
    void equip(String account, Integer id);
    @Update("UPDATE account_title_selection SET title_id=NULL WHERE account_id=#{account} AND title_id=#{id}")
    void clearEquipped(String account, int id);
    @Update("UPDATE account_title_selection SET title_id=NULL WHERE title_id=#{id}")
    void clearTitle(int id);
    @Update("UPDATE player_title SET name=#{draft.name},description=#{draft.description},sort_order=#{draft.sortOrder},enabled=#{draft.enabled} WHERE title_id=#{id}")
    void configure(int id, Draft draft);
}
