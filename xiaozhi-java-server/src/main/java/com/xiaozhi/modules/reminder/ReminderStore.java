package com.xiaozhi.modules.reminder;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.*;

@Service
public class ReminderStore {
    public static final long GRACE_MS=30*60_000L;
    public static final long FINISHED_RETENTION_MS=7*24*60*60_000L;
    private final JdbcTemplate jdbc;
    public ReminderStore(JdbcTemplate jdbc) { this.jdbc=jdbc; }
    public Map<String,Object> musicSettings(long owner) {
        var rows=jdbc.queryForList("SELECT music_volume,music_track_id FROM reminder_preferences WHERE user_id=?",owner);
        Map<String,Object> result=new HashMap<>();
        result.put("musicVolume",rows.isEmpty()?30:rows.get(0).get("music_volume"));
        result.put("musicTrackId",rows.isEmpty()?null:rows.get(0).get("music_track_id"));
        return result;
    }
    @Transactional
    public Map<String,Object> updateMusicSettings(long owner,Map<String,Object> body) {
        if(!body.containsKey("musicVolume")&&!body.containsKey("musicTrackId")) throw new IllegalArgumentException("请选择要保存的音乐设置");
        Object volume=body.get("musicVolume"),track=body.get("musicTrackId");
        if(body.containsKey("musicVolume")&&(!(volume instanceof Number n)||!Double.isFinite(n.doubleValue())||n.doubleValue()!=n.intValue()||n.intValue()<0||n.intValue()>100)) throw new IllegalArgumentException("音乐音量应为0至100的整数");
        if(body.containsKey("musicTrackId")&&track!=null&&(!(track instanceof String s)||!s.matches("[a-f0-9]{24}"))) throw new IllegalArgumentException("音乐编号无效");
        jdbc.queryForObject("SELECT id FROM sys_user WHERE id=? FOR UPDATE",Long.class,owner);
        jdbc.update("INSERT INTO reminder_preferences(user_id,music_volume) VALUES (?,30) ON DUPLICATE KEY UPDATE user_id=user_id",owner);
        if(body.containsKey("musicVolume")) jdbc.update("UPDATE reminder_preferences SET music_volume=? WHERE user_id=?",((Number)volume).intValue(),owner);
        if(body.containsKey("musicTrackId")) jdbc.update("UPDATE reminder_preferences SET music_track_id=? WHERE user_id=?",track,owner);
        return musicSettings(owner);
    }
    public int musicVolume(long owner) {
        var values=jdbc.queryForList("SELECT music_volume FROM reminder_preferences WHERE user_id=?",Integer.class,owner);
        return values.isEmpty()?30:values.get(0);
    }
    public int setMusicVolume(long owner,long volume) {
        if(volume<0||volume>100) throw new IllegalArgumentException("音乐音量应为0至100");
        jdbc.update("INSERT INTO reminder_preferences(user_id,music_volume) VALUES (?,?) ON DUPLICATE KEY UPDATE music_volume=?",owner,volume,volume);
        return (int)volume;
    }
    private static final RowMapper<Reminder> ROW=(r,n)->new Reminder(r.getString("id"),r.getLong("user_id"),
        r.getString("device_id"),r.getString("title"),r.getString("status"),r.getLong("due_at"),
        r.getLong("next_attempt_at"),r.getLong("expires_at"),r.getLong("version"),r.getString("attempt_id"),
        r.getString("last_error"),r.getLong("created_at"),r.getLong("updated_at"),r.getLong("initial_seconds"),
        r.getString("plan_id"),r.getInt("delivery_count"),r.getLong("ack_deadline"),r.getLong("requested_at"),r.getString("task_id"),r.getLong("original_due_at"),r.getLong("scheduled_at"));
    public static class Conflict extends RuntimeException { public Conflict(String message) { super(message); } }

    /** 现有设备表不是完整多租户绑定，首版只允许唯一启用账户使用提醒。 */
    public void requireOwner(long owner) {
        var owners=jdbc.queryForList("SELECT id FROM sys_user WHERE status=1",Long.class);
        if(owners.size()!=1||owners.get(0)!=owner) throw new IllegalArgumentException("提醒首版需要唯一启用账户及其设备");
    }
    public List<Map<String,Object>> devices(long owner) {
        requireOwner(owner);
        return jdbc.queryForList("SELECT mac_address AS deviceId, device_model AS name FROM device WHERE mac_address IS NOT NULL ORDER BY id LIMIT 100");
    }
    public Reminder get(long owner,String id) {
        var rows=jdbc.query("SELECT * FROM user_reminder WHERE user_id=? AND id=? AND task_id IS NULL AND history_deleted=false",ROW,owner,id);
        if(rows.isEmpty()) throw new IllegalArgumentException("提醒不存在");
        return rows.get(0);
    }
    public List<Reminder> list(long owner) {
        return jdbc.query("SELECT * FROM user_reminder WHERE user_id=? AND task_id IS NULL AND history_deleted=false ORDER BY CASE WHEN status IN ('completed','cancelled','expired','missed') THEN 1 ELSE 0 END, due_at DESC LIMIT 200",ROW,owner);
    }
    public List<Reminder> linked(long owner) { return jdbc.query("SELECT * FROM user_reminder WHERE user_id=? AND task_id IS NOT NULL",ROW,owner); }
    public List<Reminder> legacy(long owner) { return jdbc.query("SELECT * FROM user_reminder WHERE user_id=? AND task_id IS NULL AND history_deleted=false ORDER BY CASE WHEN status IN ('completed','cancelled','expired','missed') THEN 1 ELSE 0 END,due_at DESC LIMIT 200",ROW,owner); }
    public long unanswered(long owner) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM user_reminder r WHERE user_id=? AND task_id IS NULL AND history_deleted=false AND status IN ('awaiting_confirmation','retry_pending','delivery_unknown','missed')",Long.class,owner);
    }
    public long visibleCount(long owner,String device) {
        return device==null?jdbc.queryForObject("SELECT COUNT(*) FROM user_reminder WHERE user_id=? AND task_id IS NULL AND history_deleted=false",Long.class,owner):
            jdbc.queryForObject("SELECT COUNT(*) FROM user_reminder WHERE user_id=? AND device_id=? AND task_id IS NULL AND history_deleted=false",Long.class,owner,device);
    }
    public long unansweredForDevice(long owner,String device) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM user_reminder WHERE user_id=? AND device_id=? AND task_id IS NULL AND history_deleted=false AND status IN ('awaiting_confirmation','retry_pending','delivery_unknown','missed')",Long.class,owner,device);
    }
    private static final String FOLLOWUP_STAGE="(CASE status WHEN 'retry_pending' THEN next_attempt_at WHEN 'expired' THEN updated_at*8+2 WHEN 'missed' THEN updated_at*8+3 WHEN 'delivery_unknown' THEN updated_at*8+4 ELSE 0 END)";
    private static final String FOLLOWUP_ELIGIBLE="task_id IS NULL AND history_deleted=false AND (status='retry_pending' AND next_attempt_at>? OR status='delivery_unknown' OR status IN ('expired','missed') AND updated_at>=?)";
    /** Refresh only this device's expired/timeout states; never dispatch or reset retries. */
    @Transactional
    public void refreshCatchup(long owner,String device,long now) {
        jdbc.update("UPDATE user_reminder SET status='delivery_unknown',last_error='发送中断，请核实或重新安排',version=version+1,updated_at=? WHERE user_id=? AND device_id=? AND task_id IS NULL AND history_deleted=false AND status='dispatching' AND updated_at<?",now,owner,device,now-240_000);
        jdbc.update("UPDATE user_reminder SET status=CASE WHEN delivery_count>=3 THEN 'missed' ELSE 'retry_pending' END,next_attempt_at=ack_deadline+300000,last_error='未收到回应',version=version+1,updated_at=? WHERE user_id=? AND device_id=? AND task_id IS NULL AND history_deleted=false AND status='awaiting_confirmation' AND ack_deadline>0 AND ack_deadline<=?",now,owner,device,now);
        jdbc.update("UPDATE user_reminder SET status='expired',last_error='超过到期后30分钟，已停止自动补发',version=version+1,updated_at=? WHERE user_id=? AND device_id=? AND task_id IS NULL AND history_deleted=false AND status='scheduled' AND expires_at<=?",now,owner,device,now);
    }
    public List<Reminder> catchupItems(long owner,String device,long now) {
        return jdbc.query("SELECT * FROM user_reminder WHERE user_id=? AND device_id=? AND "+FOLLOWUP_ELIGIBLE+" AND followup_stage>=? ORDER BY scheduled_at,id LIMIT 3",ROW,owner,device,now,now-FINISHED_RETENTION_MS,-now);
    }
    public long catchupCount(long owner,String device,long now) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM user_reminder WHERE user_id=? AND device_id=? AND "+FOLLOWUP_ELIGIBLE+" AND followup_stage>=?",Long.class,owner,device,now,now-FINISHED_RETENTION_MS,-now);
    }
    /** Negative stage is a 90-second lease. Playback is not the user's confirmation;
     * an unanswered reminder remains eligible on the next explicit interaction. */
    @Transactional
    public boolean claimFollowup(long owner,String device,String id,String token,long stage,long now) {
        UUID.fromString(token);
        return jdbc.update("UPDATE user_reminder SET followup_stage=?,followup_token=?,version=version+1 WHERE id=? AND user_id=? AND device_id=? AND "+FOLLOWUP_ELIGIBLE+" AND "+FOLLOWUP_STAGE+"=? AND followup_stage>=?",-(now+90_000),token,id,owner,device,now,now-FINISHED_RETENTION_MS,stage,-now)==1;
    }
    public boolean followupValid(long owner,String device,String id,String token,long now) {
        return Objects.equals(jdbc.queryForObject("SELECT COUNT(*) FROM user_reminder WHERE id=? AND user_id=? AND device_id=? AND followup_token=? AND "+FOLLOWUP_ELIGIBLE+" AND followup_stage<?",Long.class,id,owner,device,token,now,now-FINISHED_RETENTION_MS,-now),1L);
    }
    public boolean followupValid(long owner,String device,String id,String token,long stage,long now) {
        return Objects.equals(jdbc.queryForObject("SELECT COUNT(*) FROM user_reminder WHERE id=? AND user_id=? AND device_id=? AND followup_token=? AND "+FOLLOWUP_ELIGIBLE+" AND "+FOLLOWUP_STAGE+"=? AND followup_stage<?",Long.class,id,owner,device,token,now,now-FINISHED_RETENTION_MS,stage,-now),1L);
    }
    public boolean finishFollowup(long owner,String device,String id,String token,long stage,long now) {
        return jdbc.update("UPDATE user_reminder SET followup_stage=?,followup_token=NULL,version=version+1 WHERE id=? AND user_id=? AND device_id=? AND followup_token=? AND "+FOLLOWUP_ELIGIBLE+" AND "+FOLLOWUP_STAGE+"=? AND followup_stage<?",stage,id,owner,device,token,now,now-FINISHED_RETENTION_MS,stage,-now)==1;
    }
    public void releaseFollowup(long owner,String device,String id,String token) {
        jdbc.update("UPDATE user_reminder SET followup_stage=0,followup_token=NULL,version=version+1 WHERE id=? AND user_id=? AND device_id=? AND followup_token=? AND followup_stage<0 AND task_id IS NULL",id,owner,device,token);
    }
    /** 保留请求去重信息，删除历史不改变周期模板。状态和版本限制防止并发重新安排被删除。 */
    public void deleteHistory(long owner,String id,long version,long now) {
        int changed=jdbc.update("UPDATE user_reminder SET history_deleted=true,updated_at=?,version=version+1 WHERE user_id=? AND id=? AND version=? AND task_id IS NULL AND history_deleted=false AND status IN ('completed','cancelled','expired','missed')",now,owner,id,version);
        if(changed!=1) throw new Conflict("只能删除已结束且未变化的记录，请刷新后重试");
    }
    /** 结束状态保留七天，从最后一次进入结束状态的时间起算；周期计划不在此表内。 */
    public int pruneFinishedHistory(long now) {
        // A future occurrence may be moved earlier than its original date.
        // Keep its ID as a tombstone so the plan cannot generate it again.
        jdbc.update("UPDATE user_reminder SET history_deleted=true WHERE task_id IS NULL AND plan_id IS NOT NULL AND requested_at<>original_due_at AND status IN ('completed','cancelled','expired','missed') AND updated_at<?",now-FINISHED_RETENTION_MS);
        return jdbc.update("DELETE FROM user_reminder WHERE task_id IS NULL AND (plan_id IS NULL OR requested_at=original_due_at) AND status IN ('completed','cancelled','expired','missed') AND updated_at<?",
            now-FINISHED_RETENTION_MS);
    }
    public static long delayMillis(long seconds) {
        if(seconds<10||seconds>7*86400L) throw new IllegalArgumentException("倒计时需为10秒至7天");
        return seconds*1000L;
    }
    @Transactional
    public Reminder create(long owner,String id,String device,String title,long seconds,long now) {
        UUID.fromString(id);
        if(title==null||title.isBlank()||title.length()>120) throw new IllegalArgumentException("提醒内容需为1至120字");
        long delay=delayMillis(seconds);
        // 账户行序列化同一用户创建；请求ID由网页保留，超时重试不会重复建提醒。
        jdbc.queryForObject("SELECT id FROM sys_user WHERE id=? FOR UPDATE",Long.class,owner);
        requireOwner(owner);
        var existing=jdbc.query("SELECT * FROM user_reminder WHERE id=?",ROW,id);
        if(!existing.isEmpty()) {
            var old=existing.get(0);
            if(old.userId()!=owner||!old.deviceId().equals(device)||!old.title().equals(title.trim())||old.initialSeconds()!=seconds) throw new Conflict("创建请求标识已使用，请刷新后重试");
            return old;
        }
        Long devices=jdbc.queryForObject("SELECT COUNT(*) FROM device WHERE mac_address=?",Long.class,device);
        if(devices==null||devices==0) throw new IllegalArgumentException("请选择已登记的设备");
        Long count=jdbc.queryForObject("SELECT COUNT(*) FROM user_reminder WHERE user_id=? AND task_id IS NULL AND status NOT IN ('completed','cancelled','expired','missed')",Long.class,owner);
        if(count!=null&&count>=100) throw new IllegalArgumentException("最多保留100条未完成提醒");
        jdbc.update("INSERT INTO user_reminder(id,user_id,device_id,title,initial_seconds,status,due_at,next_attempt_at,expires_at,version,created_at,updated_at,original_due_at,scheduled_at) VALUES (?,?,?,?,?,'scheduled',?,?,?,0,?,?,?,?)",
            id,owner,device,title.trim(),seconds,now+delay,now+delay,now+delay+GRACE_MS,now,now,now+delay,now+delay);
        return get(owner,id);
    }
    @Transactional
    public Reminder act(long owner,String id,long version,String action,long seconds,long now) {
        var old=get(owner,id);
        if(old.version()!=version) throw new Conflict("提醒已变化，请刷新后重试");
        if(old.status().equals("dispatching")) throw new Conflict("正在发送提醒，请稍后操作");
        String status;
        long due=old.dueAt();
        switch(action) {
            case "confirm" -> {
                if(!Set.of("awaiting_confirmation","delivery_unknown","retry_pending","missed","expired").contains(old.status())) throw new Conflict("此提醒尚未进入确认阶段");
                status="completed";
            }
            case "snooze" -> {
                if(!Set.of("awaiting_confirmation","delivery_unknown","expired","retry_pending","missed").contains(old.status())) throw new Conflict("此状态不能稍后提醒");
                due=now+delayMillis(seconds); status="scheduled";
            }
            case "cancel" -> {
                if(!Set.of("scheduled","awaiting_confirmation","delivery_unknown","retry_pending").contains(old.status())) throw new Conflict("此提醒已结束");
                status="cancelled";
            }
            default -> throw new IllegalArgumentException("未知操作");
        }
        int changed=jdbc.update("UPDATE user_reminder SET status=?,due_at=?,next_attempt_at=?,expires_at=?,scheduled_at=?,attempt_id=NULL,last_error=NULL,ack_deadline=0,delivery_count=0,followup_stage=0,followup_token=NULL,updated_at=?,version=version+1 WHERE id=? AND user_id=? AND version=? AND status=?",
            status,due,due,due+GRACE_MS,action.equals("snooze")?due:old.scheduledAt(),now,id,owner,version,old.status());
        if(changed!=1) throw new Conflict("提醒已变化，请刷新后重试");
        return get(owner,id);
    }
    public List<Reminder> due(long now) {
        // 中途退出时不假设未播出，避免服务重启后重复提醒。
        jdbc.update("UPDATE user_reminder SET status='delivery_unknown',last_error='发送中断，请核实或重新安排',version=version+1,updated_at=? WHERE task_id IS NULL AND status='dispatching' AND updated_at<?",now,now-240_000);
        // 兼容旧版已发送记录：没有确认期限的历史记录不自动重播。
        jdbc.update("UPDATE user_reminder SET status=CASE WHEN delivery_count>=3 THEN 'missed' ELSE 'retry_pending' END,next_attempt_at=ack_deadline+300000,last_error='未收到回应',version=version+1,updated_at=? WHERE task_id IS NULL AND status='awaiting_confirmation' AND ack_deadline>0 AND ack_deadline<=?",now,now);
        jdbc.update("UPDATE user_reminder SET status='scheduled',due_at=next_attempt_at,expires_at=next_attempt_at+1800000,attempt_id=NULL,ack_deadline=0,version=version+1,updated_at=? WHERE task_id IS NULL AND status='retry_pending' AND next_attempt_at<=?",now,now);
        jdbc.update("UPDATE user_reminder SET status='expired',last_error='超过到期后30分钟，已停止自动补发',version=version+1,updated_at=? WHERE task_id IS NULL AND status='scheduled' AND expires_at<=?",now,now);
        // Return all currently due reminders so a device can receive a numbered batch.
        // Future reminders are never announced early, even when only a minute apart.
        return jdbc.query("SELECT * FROM user_reminder r WHERE r.status='scheduled' AND r.next_attempt_at<=? AND r.expires_at>? AND r.task_id IS NULL AND r.history_deleted=false "
            +"ORDER BY r.next_attempt_at,r.id LIMIT 128",ROW,now,now);
    }
    @Transactional
    public boolean claim(Reminder reminder,String attempt,long now) {
        return jdbc.update("UPDATE user_reminder SET status='dispatching',attempt_id=?,updated_at=?,version=version+1 WHERE id=? AND status='scheduled' AND version=? AND expires_at>? AND task_id IS NULL",
            attempt,now,reminder.id(),reminder.version(),now)==1;
    }
    public void finish(Reminder reminder,String attempt,String outcome,String error,long now) {
        String status=switch(outcome) { case "sent"->"awaiting_confirmation"; case "retry"->now>=reminder.expiresAt()?"expired":"scheduled"; default->"delivery_unknown"; };
        jdbc.update("UPDATE user_reminder SET status=?,last_error=?,next_attempt_at=?,updated_at=?,ack_deadline=?,delivery_count=delivery_count+?,version=version+1 WHERE id=? AND task_id IS NULL AND status='dispatching' AND attempt_id=?",
            status,error,now+15_000,now,outcome.equals("sent")?now+60_000:0,outcome.equals("sent")?1:0,reminder.id(),attempt);
    }

    public List<Reminder> forDevice(long owner,String device) {
        return jdbc.query("SELECT * FROM user_reminder r WHERE user_id=? AND device_id=? AND task_id IS NULL AND history_deleted=false ORDER BY CASE WHEN status IN ('completed','cancelled','expired','missed') THEN 1 ELSE 0 END, updated_at DESC LIMIT 200",ROW,owner,device);
    }
    /** 唤醒仅延长一次回答窗口，不确认提醒，不允许旧 attempt 修改新一轮。 */
    public void listening(long owner,String id,String attempt,long now) {
        jdbc.update("UPDATE user_reminder SET ack_deadline=GREATEST(ack_deadline,?),version=version+1 WHERE id=? AND user_id=? AND attempt_id=? AND task_id IS NULL AND status='awaiting_confirmation' AND ack_deadline>? AND ack_deadline<?",
            now+30_000,id,owner,attempt,now,now+30_000);
    }
    public static void validateDate(long at,long now) {
        if(at<=now||at>now+5*366*86400_000L) throw new IllegalArgumentException("请选择未来5年内的明确日期和时间");
    }
    @Transactional
    public Reminder createAt(long owner,String id,String device,String title,long at,long now) {
        UUID.fromString(id);
        if(title==null||title.isBlank()||title.length()>120) throw new IllegalArgumentException("提醒内容需为1至120字");
        jdbc.queryForObject("SELECT id FROM sys_user WHERE id=? FOR UPDATE",Long.class,owner);
        requireOwner(owner);
        var existing=jdbc.query("SELECT * FROM user_reminder WHERE id=?",ROW,id);
        // 最初请求的绝对时间单独保存；延后后仍能正确识别原请求的重试。
        if(!existing.isEmpty()) {
            var old=existing.get(0);
            if(old.userId()!=owner||!old.deviceId().equals(device)||!old.title().equals(title.trim())||old.requestedAt()!=at) throw new Conflict("创建请求标识已使用");
            return old;
        }
        validateDate(at,now);
        Long count=jdbc.queryForObject("SELECT COUNT(*) FROM device WHERE mac_address=?",Long.class,device);
        if(count==null||count==0) throw new IllegalArgumentException("请选择已登记的设备");
        Long active=jdbc.queryForObject("SELECT COUNT(*) FROM user_reminder WHERE user_id=? AND task_id IS NULL AND status NOT IN ('completed','cancelled','expired','missed')",Long.class,owner);
        if(active!=null&&active>=100) throw new IllegalArgumentException("最多保留100条未结束提醒");
        jdbc.update("INSERT INTO user_reminder(id,user_id,device_id,title,initial_seconds,status,due_at,next_attempt_at,expires_at,version,created_at,updated_at,requested_at,original_due_at,scheduled_at) VALUES (?,?,?,?,0,'scheduled',?,?,?,0,?,?,?,?,?)",id,owner,device,title.trim(),at,at,at+GRACE_MS,now,now,at,at,at);
        return get(owner,id);
    }
    @Transactional
    public Reminder reschedule(long owner,String id,long version,String title,long at,long now) {
        validateDate(at,now);
        if(title==null||title.isBlank()||title.length()>120) throw new IllegalArgumentException("提醒内容需为1至120字");
        get(owner,id); // Reject archived todo-linked records before changing anything.
        int changed=jdbc.update("UPDATE user_reminder SET title=?,status='scheduled',due_at=?,next_attempt_at=?,expires_at=?,scheduled_at=?,ack_deadline=0,delivery_count=0,followup_stage=0,followup_token=NULL,attempt_id=NULL,last_error=NULL,version=version+1,updated_at=? WHERE id=? AND user_id=? AND version=? AND status<>'dispatching'",title.trim(),at,at,at+GRACE_MS,at,now,id,owner,version);
        if(changed!=1) throw new Conflict("提醒已变化或正在发送，请刷新后重试");
        return get(owner,id);
    }
}
