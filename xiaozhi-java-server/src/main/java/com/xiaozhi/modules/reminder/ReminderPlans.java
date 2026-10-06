package com.xiaozhi.modules.reminder;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.*;
import java.util.*;

/** 周期模板与 occurrence 分离；首版固定北京时间，周规则锚定首次日期的星期。 */
@Service
public class ReminderPlans {
    private static final ZoneId ZONE=ZoneId.of("Asia/Shanghai");
    private final JdbcTemplate jdbc;
    private final ReminderStore store;
    public ReminderPlans(JdbcTemplate jdbc,ReminderStore store) { this.jdbc=jdbc; this.store=store; }
    public record Plan(String id,String deviceId,String title,String recurrence,long initialAt,long nextAt,String status,long version,
                       long userId,boolean taskEnabled,boolean taskDateEnabled,Long deadlineOffset,Long remindOffset) {}
    private static Long nullable(java.sql.ResultSet r,String column) throws java.sql.SQLException { long value=r.getLong(column); return r.wasNull()?null:value; }
    private static final org.springframework.jdbc.core.RowMapper<Plan> ROW=(r,n)->new Plan(r.getString("id"),r.getString("device_id"),r.getString("title"),r.getString("recurrence"),r.getLong("initial_at"),r.getLong("next_at"),r.getString("status"),r.getLong("version"),r.getLong("user_id"),r.getBoolean("task_enabled"),r.getBoolean("task_date_enabled"),nullable(r,"deadline_offset"),nullable(r,"remind_offset"));
    public List<Plan> list(long owner) { return jdbc.query("SELECT * FROM reminder_plan WHERE user_id=? AND task_enabled=false ORDER BY CASE WHEN status='cancelled' THEN 1 ELSE 0 END,created_at DESC LIMIT 200",ROW,owner); }
    public Plan get(long owner,String id) { return find(owner,id,false); }
    public Plan lock(long owner,String id) { return find(owner,id,true); }
    private Plan find(long owner,String id,boolean lock) {
        var values=jdbc.query("SELECT * FROM reminder_plan WHERE task_enabled=false AND user_id=? AND id=?"+(lock?" FOR UPDATE":""),ROW,owner,id);
        if(values.isEmpty()) throw new IllegalArgumentException("周期计划不存在"); return values.get(0);
    }
    public static boolean validRule(String rule) {
        return rule!=null&&(Set.of("daily","weekly","weekdays").contains(rule)||rule.matches("weekly:[1-7](,[1-7]){0,6}"));
    }
    public static boolean matchesDay(String rule,int isoDay,int anchorDay) {
        return rule.equals("daily")||rule.equals("weekly")&&isoDay==anchorDay||rule.equals("weekdays")&&isoDay<=5
            ||rule.startsWith("weekly:")&&Arrays.asList(rule.substring(7).split(",")).contains(Integer.toString(isoDay));
    }
    public static long next(long anchor,String rule,long after) {
        if(!validRule(rule)) throw new IllegalArgumentException("请选择每天、每周一天或每周多天");
        ZoneId zone=ZoneId.of("Asia/Shanghai");
        var start=Instant.ofEpochMilli(anchor).atZone(zone);
        var candidate=Instant.ofEpochMilli(Math.max(after,anchor-1)).atZone(zone).toLocalDate().atTime(start.toLocalTime()).atZone(zone);
        for(int i=0;i<9;i++,candidate=candidate.plusDays(1)) {
            if(candidate.toInstant().toEpochMilli()<=after||candidate.toInstant().toEpochMilli()<anchor) continue;
            var day=candidate.getDayOfWeek();
            if(matchesDay(rule,day.getValue(),start.getDayOfWeek().getValue())) return candidate.toInstant().toEpochMilli();
        }
        throw new IllegalArgumentException("周期计算失败");
    }
    @Transactional
    public Plan create(long owner,String id,String device,String title,long at,String recurrence,long now) {
        UUID.fromString(id);
        jdbc.queryForObject("SELECT id FROM sys_user WHERE id=? FOR UPDATE",Long.class,owner);
        store.requireOwner(owner);
        var old=jdbc.query("SELECT * FROM reminder_plan WHERE id=? AND user_id=?",ROW,id,owner);
        if(!old.isEmpty()) {
            var p=old.get(0);
            if(!p.deviceId().equals(device)||!p.title().equals(title)||p.initialAt()!=at||!p.recurrence().equals(recurrence)) throw new ReminderStore.Conflict("创建请求标识已使用");
            return p;
        }
        ReminderStore.validateDate(at,now);
        next(at,recurrence,at);
        if(!matchesDay(recurrence,Instant.ofEpochMilli(at).atZone(ZONE).getDayOfWeek().getValue(),Instant.ofEpochMilli(at).atZone(ZONE).getDayOfWeek().getValue())) throw new IllegalArgumentException("首次日期不在所选星期内");
        if(title==null||title.isBlank()||title.length()>120) throw new IllegalArgumentException("提醒内容需为1至120字");
        if(!Objects.equals(jdbc.queryForObject("SELECT COUNT(*) FROM device WHERE mac_address=?",Long.class,device),1L)) throw new IllegalArgumentException("请选择已登记的设备");
        Long active=jdbc.queryForObject("SELECT COUNT(*) FROM reminder_plan WHERE user_id=? AND task_enabled=false AND status IN ('active','paused')",Long.class,owner);
        if(active!=null&&active>=100) throw new IllegalArgumentException("最多保留100条有效周期计划");
        if(recurrence.equals("weekdays")&&Instant.ofEpochMilli(at).atZone(ZoneId.of("Asia/Shanghai")).getDayOfWeek().getValue()>5) throw new IllegalArgumentException("周一至周五计划的首次日期应为周一至周五");
        jdbc.update("INSERT INTO reminder_plan(id,user_id,device_id,title,recurrence,initial_at,next_at,created_at) VALUES (?,?,?,?,?,?,?,?)",id,owner,device,title,recurrence,at,at,now);
        return list(owner).stream().filter(p->p.id().equals(id)).findFirst().orElseThrow();
    }
    @Transactional
    public void action(long owner,String id,long version,String action,Long at,String title,String recurrence,long now) {
        var rows=jdbc.query("SELECT * FROM reminder_plan WHERE task_enabled=false AND id=? AND user_id=? FOR UPDATE",ROW,id,owner);
        if(rows.isEmpty()) throw new IllegalArgumentException("计划不存在");
        var p=rows.get(0);
        if(p.version()!=version||p.status().equals("cancelled")) throw new ReminderStore.Conflict("计划已变化或已取消");
        String status=switch(action) { case "pause"->"paused"; case "resume","edit"->"active"; case "cancel"->"cancelled"; default->throw new IllegalArgumentException("未知计划操作"); };
        long anchor=p.initialAt(),next=p.nextAt();
        String name=p.title(),rule=p.recurrence();
        if(action.equals("resume")) next=next(anchor,rule,now);
        if(action.equals("edit")) {
            if(at==null) throw new IllegalArgumentException("需要首次日期时间");
            ReminderStore.validateDate(at,now);
            if(title==null||title.isBlank()||title.length()>120) throw new IllegalArgumentException("需要提醒内容");
            next(at,recurrence,at); if(!matchesDay(recurrence,Instant.ofEpochMilli(at).atZone(ZONE).getDayOfWeek().getValue(),Instant.ofEpochMilli(at).atZone(ZONE).getDayOfWeek().getValue())) throw new IllegalArgumentException("首次日期不在所选星期内"); anchor=at; next=at; name=title; rule=recurrence;
            if(rule.equals("weekdays")&&Instant.ofEpochMilli(at).atZone(ZoneId.of("Asia/Shanghai")).getDayOfWeek().getValue()>5) throw new IllegalArgumentException("首次日期应为周一至周五");
        }
        jdbc.update("UPDATE reminder_plan SET status=?,initial_at=?,next_at=?,title=?,recurrence=?,version=version+1 WHERE id=? AND user_id=? AND version=?",status,anchor,next,name,rule,id,owner,version);
        // 已生成的本次提醒独立保留，网页明确告知；暂停/修改只影响后续发生。
    }
    @Transactional
    public Reminder toOnce(long owner,String id,String device,long version,long at,long now) {
        jdbc.queryForObject("SELECT id FROM sys_user WHERE id=? FOR UPDATE",Long.class,owner);
        var p=lock(owner,id);
        if(!p.deviceId().equals(device)||p.taskEnabled()) throw new IllegalArgumentException("设备与独立提醒计划不匹配");
        String reminderId=UUID.nameUUIDFromBytes(("plan-to-once:"+id+":"+version).getBytes(java.nio.charset.StandardCharsets.UTF_8)).toString();
        var existing=jdbc.queryForList("SELECT id FROM user_reminder WHERE id=? AND user_id=?",String.class,reminderId,owner);
        if(p.status().equals("cancelled")&&p.version()==version+1&&!existing.isEmpty())
            return store.createAt(owner,reminderId,device,p.title(),at,now);
        if(p.version()!=version||p.status().equals("cancelled")) throw new ReminderStore.Conflict("计划已变化，请重新查询后修改");
        var item=store.createAt(owner,reminderId,device,p.title(),at,now);
        int changed=jdbc.update("UPDATE reminder_plan SET status='cancelled',version=version+1 WHERE id=? AND user_id=? AND version=?",id,owner,version);
        if(changed!=1) throw new ReminderStore.Conflict("计划已变化，请重新查询后修改");
        return item;
    }
    @Transactional
    public Reminder editOccurrence(long owner,String id,String device,long version,long occurrenceAt,long at,long now) {
        jdbc.queryForObject("SELECT id FROM sys_user WHERE id=? FOR UPDATE",Long.class,owner);
        var p=lock(owner,id);
        if(!p.deviceId().equals(device)||p.taskEnabled()) throw new IllegalArgumentException("设备与独立提醒计划不匹配");
        String reminderId=UUID.nameUUIDFromBytes((id+":"+occurrenceAt).getBytes(java.nio.charset.StandardCharsets.UTF_8)).toString();
        var existing=jdbc.queryForList("SELECT id FROM user_reminder WHERE id=? AND user_id=? AND plan_id=? AND original_due_at=?",String.class,reminderId,owner,id,occurrenceAt);
        if(!existing.isEmpty()) return store.createAt(owner,reminderId,device,p.title(),at,now);
        if(p.version()!=version||!p.status().equals("active")) throw new ReminderStore.Conflict("计划已变化或暂停，请重新查询后修改");
        if(occurrenceAt<p.nextAt()||next(p.initialAt(),p.recurrence(),occurrenceAt-1)!=occurrenceAt)
            throw new ReminderStore.Conflict("这一天没有对应的周期提醒");
        ReminderStore.validateDate(occurrenceAt,now);
        store.createAt(owner,reminderId,device,p.title(),at,now);
        jdbc.update("UPDATE user_reminder SET plan_id=?,original_due_at=? WHERE id=? AND user_id=?",id,occurrenceAt,reminderId,owner);
        int changed=jdbc.update("UPDATE reminder_plan SET version=version+1 WHERE id=? AND user_id=? AND version=?",id,owner,version);
        if(changed!=1) throw new ReminderStore.Conflict("计划已变化，请重新查询后修改");
        return store.get(owner,reminderId);
    }
    @Transactional
    public void materialize(long now) {
        var owners=jdbc.queryForList("SELECT id FROM sys_user WHERE status=1",Long.class);
        long owner=owners.size()==1?owners.get(0):-1L;
        var rows=jdbc.query("SELECT * FROM reminder_plan WHERE task_enabled=false AND user_id=? AND status='active' AND next_at<=? ORDER BY next_at LIMIT 20 FOR UPDATE",ROW,owner,now);
        for(var p:rows) {
            String id=UUID.nameUUIDFromBytes((p.id()+":"+p.nextAt()).getBytes(java.nio.charset.StandardCharsets.UTF_8)).toString();
            String status=now-p.nextAt()>ReminderStore.GRACE_MS?"missed":"scheduled";
            // An edited future occurrence already owns this deterministic ID.
            jdbc.update("INSERT INTO user_reminder(id,user_id,device_id,title,initial_seconds,status,due_at,next_attempt_at,expires_at,version,created_at,updated_at,plan_id,requested_at,original_due_at,scheduled_at) VALUES (?,?,?,?,0,?,?,?,?,0,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE id=id",id,owner,p.deviceId(),p.title(),status,p.nextAt(),p.nextAt(),p.nextAt()+ReminderStore.GRACE_MS,now,now,p.id(),p.nextAt(),p.nextAt(),p.nextAt());
            // 停机多天不补播所有历史轮次，只记本轮错过并前进到未来。
            jdbc.update("UPDATE reminder_plan SET next_at=?,version=version+1 WHERE id=?",next(p.initialAt(),p.recurrence(),now),p.id());
        }
    }
}
