package org.t7rekindle.server.persistence;

import java.util.List;
import org.apache.ibatis.annotations.*;
import org.t7rekindle.server.domain.Models.*;

@Mapper
public interface AdminMapper {
    @Select("SELECT * FROM admin_account WHERE admin_id=1")
    Admin admin();

    @Select("SELECT * FROM admin_account WHERE admin_id=1 FOR UPDATE")
    Admin lockAdmin();

    @Insert("INSERT INTO admin_account(admin_id,login_name,password_hash) VALUES(1,'admin',#{hash})")
    void createAdmin(String hash);

    @Update("UPDATE admin_account SET password_hash=#{hash},must_change_password=#{mustChange},auth_epoch=auth_epoch+1 WHERE admin_id=1")
    void changePassword(String hash, boolean mustChange);

    @Insert("INSERT INTO audit_log(actor,action,target) VALUES(#{actor},#{action},#{target})")
    void audit(String actor, String action, String target);

    @Select("SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT 50 OFFSET #{offset}")
    List<Audit> audits(int offset);
}
