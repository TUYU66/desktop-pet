package com.xiaozhi.modules.reminder;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.*;

/** Immutable request receipt outlives mutable delivery state and seven-day history. */
@Service
public class ReminderCreations {
    private final JdbcTemplate jdbc;
    private final ReminderStore store;
    private final ReminderPlans plans;
    public ReminderCreations(JdbcTemplate jdbc,ReminderStore store,ReminderPlans plans) { this.jdbc=jdbc; this.store=store; this.plans=plans; }

    @Transactional
    public Map<String,Object> create(long owner,String id,String device,String title,Long at,String rule,Long seconds,long now) {
        UUID.fromString(id);
        jdbc.queryForObject("SELECT id FROM sys_user WHERE id=? FOR UPDATE",Long.class,owner);
        store.requireOwner(owner);
        var rows=jdbc.queryForList("SELECT * FROM reminder_creation WHERE id=?",id);
        if(!rows.isEmpty()) {
            var old=rows.get(0);
            if(((Number)old.get("user_id")).longValue()!=owner||!Objects.equals(old.get("device_id"),device)
                    ||!Objects.equals(old.get("title"),title)||!Objects.equals(old.get("recurrence"),rule)
                    ||!sameNumber(old.get("trigger_at"),at)||!sameNumber(old.get("seconds"),seconds))
                throw new ReminderStore.Conflict("创建请求编号已用于其他内容、设备或时间，请核实原请求");
        } else {
            // Stores also reject collisions with legacy records; no new ID is generated here.
            if(at==null) store.create(owner,id,device,title,Objects.requireNonNull(seconds),now);
            else if(rule.equals("once")) store.createAt(owner,id,device,title,at,now);
            else plans.create(owner,id,device,title,at,rule,now);
            jdbc.update("INSERT INTO reminder_creation(id,user_id,device_id,title,trigger_at,recurrence,seconds,created_at) VALUES (?,?,?,?,?,?,?,?)",id,owner,device,title,at,rule,seconds,now);
        }
        Map<String,Object> creation=new HashMap<>();
        creation.put("requestId",id); creation.put("deviceId",device); creation.put("title",title);
        creation.put("triggerAt",at); creation.put("recurrence",rule); creation.put("seconds",seconds);
        var current=rule.equals("once")?
            jdbc.queryForList("SELECT id,title,status,due_at AS dueAt,scheduled_at AS scheduledAt,version,history_deleted AS historyDeleted FROM user_reminder WHERE id=? AND user_id=? AND task_id IS NULL",id,owner):
            jdbc.queryForList("SELECT id,title,status,initial_at AS initialAt,next_at AS nextAt,recurrence,version FROM reminder_plan WHERE id=? AND user_id=? AND task_enabled=false",id,owner);
        return Map.of("id",id,"title",title,"deviceId",device,"creation",creation,
            "current",current.isEmpty()?Map.of("status","history_removed"):current.get(0));
    }
    private static boolean sameNumber(Object value,Long expected) {
        return expected==null?value==null:value instanceof Number n&&n.longValue()==expected;
    }
}
