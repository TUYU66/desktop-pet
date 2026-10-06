package com.xiaozhi.modules.reminder;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.transaction.annotation.Transactional;
import java.time.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.function.Supplier;

/** Retired todo implementation retained for historical reference; not a Spring service. */
public class TaskStore {
    public static final ZoneId ZONE=ZoneId.of("Asia/Shanghai");
    private final JdbcTemplate jdbc;
    private final ReminderStore reminders;
    private final ReminderPlans plans;
    private final ObjectMapper json;
    public TaskStore(JdbcTemplate jdbc,ReminderStore reminders,ReminderPlans plans,ObjectMapper json) {
        this.jdbc=jdbc; this.reminders=reminders; this.plans=plans; this.json=json;
    }
    public static class NotFound extends RuntimeException { public NotFound() { super("任务不存在或已删除"); } }
    private record Row(String id,long owner,String title,String date,Long deadline,Long remind,String device,
                       String reminder,String status,long version,String plan,Long occurrence,boolean deleted,long created,long updated) {}
    public record Task(String id,String title,String status,long version,String taskDate,Long deadlineAt,Long remindAt,
                       String deviceId,String reminderId,boolean reminderEnabled,Reminder reminder,String planId,
                       Long occurrenceAt,boolean virtual,long createdAt,long updatedAt) {}
    private record Fields(String title,String date,Long deadline,Long remind,String device) {}
    private static Long nullable(java.sql.ResultSet r,String name) throws java.sql.SQLException {
        long value=r.getLong(name); return r.wasNull()?null:value;
    }
    private static final RowMapper<Row> ROW=(r,n)->new Row(r.getString("id"),r.getLong("user_id"),r.getString("title"),
        r.getString("task_date"),nullable(r,"deadline_at"),nullable(r,"remind_at"),r.getString("device_id"),r.getString("reminder_id"),
        r.getString("status"),r.getLong("version"),r.getString("plan_id"),nullable(r,"occurrence_at"),r.getBoolean("deleted"),r.getLong("created_at"),r.getLong("updated_at"));
    private Task view(Row r,Reminder reminder) {
        return new Task(r.id(),r.title(),r.status(),r.version(),r.date(),r.deadline(),r.remind(),r.device(),r.reminder(),
            r.status().equals("open")&&r.remind()!=null,reminder,r.plan(),r.occurrence(),false,r.created(),r.updated());
    }
    private Row row(long owner,String id,boolean lock) {
        var found=jdbc.query("SELECT * FROM user_todo WHERE user_id=? AND id=?"+(lock?" FOR UPDATE":""),ROW,owner,id);
        return found.isEmpty()?null:found.get(0);
    }
    private Task view(Row r) {
        Reminder reminder=null;
        if(r.reminder()!=null) {
            try { reminder=reminders.get(r.owner(),r.reminder()); } catch(IllegalArgumentException ignored) { }
        }
        return view(r,reminder);
    }
    public Task get(long owner,String id) {
        Row r=row(owner,id,false);
        if(r==null||r.deleted()) throw new NotFound();
        return view(r);
    }
    private String encode(Object value) {
        try { return json.writeValueAsString(value); } catch(Exception e) { throw new IllegalStateException("任务回执无法保存",e); }
    }
    private Map<String,Object> once(long owner,String route,Map<String,Object> body,Supplier<Map<String,Object>> action) {
        String id=text(body,"requestId"); UUID.fromString(id);
        jdbc.queryForObject("SELECT id FROM sys_user WHERE id=? FOR UPDATE",Long.class,owner);
        String payload=encode(new TreeMap<>(body))+"\n"+route;
        var old=jdbc.queryForList("SELECT request_body,response_body FROM task_operation WHERE user_id=? AND request_id=?",owner,id);
        if(!old.isEmpty()) {
            if(!Objects.equals(old.get(0).get("request_body"),payload)) throw new ReminderStore.Conflict("操作标识已用于其他内容");
            try { return json.readValue((String)old.get(0).get("response_body"),new TypeReference<Map<String,Object>>(){}); }
            catch(Exception e) { throw new IllegalStateException("任务回执读取失败",e); }
        }
        Map<String,Object> result=action.get();
        jdbc.update("INSERT INTO task_operation(user_id,request_id,request_body,response_body,created_at) VALUES (?,?,?,?,?)",owner,id,payload,encode(result),System.currentTimeMillis());
        return result;
    }
    private static Map<String,Object> result(Task task,ReminderPlans.Plan plan,boolean stopped) {
        Map<String,Object> value=new HashMap<>(); value.put("item",task); value.put("plan",plan); value.put("remindersStopped",stopped); return value;
    }
    public static String text(Map<String,Object> body,String key) {
        if(!(body.get(key) instanceof String s)||s.isBlank()||s.length()>120) throw new IllegalArgumentException("字段无效："+key);
        return s.trim();
    }
    public static long number(Map<String,Object> body,String key) {
        if(!(body.get(key) instanceof Number n)||!Double.isFinite(n.doubleValue())||n.longValue()<0||n.doubleValue()!=n.longValue()) throw new IllegalArgumentException("字段应为非负整数："+key);
        return n.longValue();
    }
    private static Long optionalNumber(Map<String,Object> body,String key) { return body.get(key)==null?null:number(body,key); }
    private static Fields fields(Map<String,Object> body) {
        String title=text(body,"title"),date=null;
        if(body.get("taskDate")!=null) {
            if(!(body.get("taskDate") instanceof String s)||!s.matches("\\d{4}-\\d{2}-\\d{2}")) throw new IllegalArgumentException("任务日期格式应为YYYY-MM-DD");
            try { date=LocalDate.parse(s).toString(); } catch(DateTimeException e) { throw new IllegalArgumentException("任务日期无效"); }
        }
        if(body.containsKey("reminderEnabled")&&!(body.get("reminderEnabled") instanceof Boolean)) throw new IllegalArgumentException("提醒开关无效");
        boolean enabled=Boolean.TRUE.equals(body.get("reminderEnabled"));
        return new Fields(title,date,optionalNumber(body,"deadlineAt"),enabled?optionalNumber(body,"remindAt"):null,enabled?text(body,"deviceId"):null);
    }
    private static void validateDeadline(Fields f,long now) {
        LocalDate today=Instant.ofEpochMilli(now).atZone(ZONE).toLocalDate();
        if(f.date()==null||f.deadline()==null) throw new IllegalArgumentException("请选择待办开始日期和截止日期");
        LocalDate start=LocalDate.parse(f.date());
        var end=Instant.ofEpochMilli(f.deadline()).atZone(ZONE);
        LocalDate last=end.toLocalDate().minusDays(1);
        if(!end.toLocalTime().equals(LocalTime.MIDNIGHT)||start.isBefore(today)||start.isAfter(today.plusDays(7))||last.isBefore(start)||last.isAfter(today.plusDays(7)))
            throw new IllegalArgumentException("待办日期须在今天至未来7天内，截止日期不能早于开始日期");
        if(f.remind()!=null) throw new IllegalArgumentException("待办不设置提醒，请在日程提醒中安排");
    }
    private void validate(long owner,Fields f,boolean enabled,long now) {
        if(enabled) throw new IllegalArgumentException("待办不设置提醒，请在日程提醒中安排");
        validateDeadline(f,now);
    }
    @Transactional
    public Map<String,Object> create(long owner,Map<String,Object> body,long now) {
        return once(owner,"create",body,()->{
            Fields f=fields(body); validate(owner,f,Boolean.TRUE.equals(body.get("reminderEnabled")),now);
            String id=text(body,"requestId"),rule=body.getOrDefault("recurrence","once").toString();
            if(!Set.of("once","daily","weekly","weekdays").contains(rule)) throw new IllegalArgumentException("周期无效");
            if(!rule.equals("once")) throw new IllegalArgumentException("周期只用于日程提醒，待办不重复");
            Long count=jdbc.queryForObject("SELECT COUNT(*) FROM user_todo WHERE user_id=? AND status='open' AND deleted=false",Long.class,owner);
            if(count!=null&&count>=1000) throw new IllegalArgumentException("最多保留1000条待办任务");
            if(row(owner,id,false)!=null) throw new ReminderStore.Conflict("任务标识已使用");
            String reminder=f.remind()==null?null:UUID.nameUUIDFromBytes(("task-reminder:"+id).getBytes(StandardCharsets.UTF_8)).toString();
            jdbc.update("INSERT INTO user_todo(id,user_id,title,task_date,deadline_at,device_id,reminder_id,remind_at,status,version,request_body,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'open',0,?,?,?)",
                id,owner,f.title(),f.date(),f.deadline(),f.device(),reminder,f.remind(),encode(new TreeMap<>(body)),now,now);
            if(reminder!=null) insertReminder(jdbc,owner,id,reminder,null,f.title(),f.device(),f.remind(),now);
            return result(view(row(owner,id,false)),null,false);
        });
    }
    private static void insertReminder(JdbcTemplate jdbc,long owner,String task,String id,String plan,String title,String device,long at,long now) {
        jdbc.update("INSERT IGNORE INTO user_reminder(id,user_id,device_id,title,initial_seconds,status,due_at,next_attempt_at,expires_at,version,created_at,updated_at,plan_id,requested_at,original_due_at,task_id) VALUES (?,?,?,?,0,?,?,?,?,0,?,?,?,?,?,?)",
            id,owner,device,title,now-at>ReminderStore.GRACE_MS?"missed":"scheduled",at,at,at+ReminderStore.GRACE_MS,now,now,plan,at,at,task);
    }
    public static String occurrenceId(String plan,long at) { return UUID.nameUUIDFromBytes(("task:"+plan+":"+at).getBytes(StandardCharsets.UTF_8)).toString(); }
    public static void materializeOccurrence(JdbcTemplate jdbc,ReminderPlans.Plan p,long at,long now) {
        String id=occurrenceId(p.id(),at),reminder=p.remindOffset()==null?null:UUID.nameUUIDFromBytes((p.id()+":"+at).getBytes(StandardCharsets.UTF_8)).toString();
        String date=p.taskDateEnabled()?Instant.ofEpochMilli(at).atZone(ZONE).toLocalDate().toString():null;
        Long deadline=p.deadlineOffset()==null?null:at+p.deadlineOffset(),remind=p.remindOffset()==null?null:at+p.remindOffset();
        jdbc.update("INSERT IGNORE INTO user_todo(id,user_id,title,task_date,deadline_at,device_id,reminder_id,remind_at,status,version,request_body,created_at,updated_at,plan_id,occurrence_at) VALUES (?,?,?,?,?,?,?,?,'open',0,'{}',?,?,?,?)",
            id,p.userId(),p.title(),date,deadline,p.deviceId().isEmpty()?null:p.deviceId(),reminder,remind,now,now,p.id(),at);
        if(reminder!=null&&Objects.equals(jdbc.queryForObject("SELECT COUNT(*) FROM user_todo WHERE id=? AND status='open' AND deleted=false AND remind_at IS NOT NULL AND (deadline_at IS NULL OR deadline_at>?)",Long.class,id,now),1L))
            insertReminder(jdbc,p.userId(),id,reminder,p.id(),p.title(),p.deviceId(),remind,now);
    }
    private Row resolve(long owner,String id,Map<String,Object> body,long now) {
        Row existing=row(owner,id,false);
        if(existing!=null) { existing=row(owner,id,true); if(existing.deleted()) throw new NotFound(); return existing; }
        if(body.get("planId")==null||body.get("occurrenceAt")==null) throw new NotFound();
        String plan=text(body,"planId"); long at=number(body,"occurrenceAt");
        ReminderPlans.Plan p=plans.lock(owner,plan);
        existing=row(owner,id,true);
        if(existing!=null) { if(existing.deleted()) throw new NotFound(); return existing; }
        if(!p.taskEnabled()||!p.status().equals("active")||at<p.nextAt()||at>now+5*366*86400_000L
            ||!id.equals(occurrenceId(plan,at))||ReminderPlans.next(p.initialAt(),p.recurrence(),at-1)!=at)
            throw new ReminderStore.Conflict("周期任务已变化，请刷新后重试");
        materializeOccurrence(jdbc,p,at,now); return row(owner,id,true);
    }
    /** Move unfinished todos to expired history; hide ended records after seven days. */
    // Retired feature: preserved history is not expired or pruned automatically.
    @Transactional
    public void clearDeadlineTasks() {
        long now=System.currentTimeMillis();
        var owners=jdbc.queryForList("SELECT DISTINCT user_id FROM user_todo WHERE deleted=false AND status='open' AND deadline_at<=? ORDER BY user_id",Long.class,now);
        for(long owner:owners) {
            // Same lock order as task mutations: owner, task, associated reminder.
            jdbc.queryForObject("SELECT id FROM sys_user WHERE id=? FOR UPDATE",Long.class,owner);
            var expired=jdbc.query("SELECT * FROM user_todo WHERE user_id=? AND deleted=false AND status='open' AND deadline_at<=? FOR UPDATE",ROW,owner,now);
            for(Row r:expired) {
                stop(r,now);
                jdbc.update("UPDATE user_todo SET status='expired',remind_at=NULL,version=version+1,updated_at=deadline_at WHERE id=? AND user_id=?",r.id(),owner);
            }
        }
        jdbc.update("UPDATE user_todo SET deleted=true,version=version+1 WHERE deleted=false AND status IN ('completed','cancelled','expired') AND updated_at<?",now-7*86400_000L);
    }
    private void stop(Row r,long now) {
        jdbc.update("UPDATE user_reminder SET status='cancelled',attempt_id=NULL,ack_deadline=0,followup_token=NULL,last_error=NULL,version=version+1,updated_at=? WHERE user_id=? AND task_id=? AND status NOT IN ('completed','cancelled','expired','missed')",now,r.owner(),r.id());
    }
    @Transactional
    public Map<String,Object> action(long owner,String id,Map<String,Object> body,long now) {
        return once(owner,"task:"+id,body,()->{
            Row r=resolve(owner,id,body,now);
            if(r.status().equals("open")&&r.deadline()!=null&&r.deadline()<=now) throw new ReminderStore.Conflict("待办已到期，请刷新查看历史");
            if(r.version()!=number(body,"version")) throw new ReminderStore.Conflict("任务已变化，请刷新后重试");
            String action=text(body,"action"),status=r.status(); boolean deleted=false,stopped=false;
            Fields f=new Fields(r.title(),r.date(),r.deadline(),r.remind(),r.device()); String reminder=r.reminder();
            if(Set.of("confirm","snooze","disable_reminder").contains(action)) throw new IllegalArgumentException("待办不包含提醒，请在日程提醒中操作");
            switch(action) {
                case "complete" -> { if(!status.equals("open")) throw new ReminderStore.Conflict("任务已结束"); status="completed"; stopped=true; }
                case "cancel" -> { if(status.equals("cancelled")) throw new ReminderStore.Conflict("任务已取消"); status="cancelled"; stopped=true; }
                case "delete" -> { deleted=true; stopped=true; }
                case "restore" -> { if(status.equals("open")) throw new ReminderStore.Conflict("任务已经是待办"); validateDeadline(f,now); status="open"; stopped=true; }
                case "edit" -> {
                    if(!status.equals("open")) throw new ReminderStore.Conflict("请先恢复为待办再编辑");
                    if(Boolean.TRUE.equals(body.get("reminderEnabled"))) throw new IllegalArgumentException("待办不设置提醒，请在日程提醒中安排");
                    f=fields(body);
                    stopped=!Objects.equals(f.remind(),r.remind())||!Objects.equals(f.device(),r.device());
                    if(stopped) validate(owner,f,false,now);
                    else if(!Objects.equals(f.date(),r.date())||!Objects.equals(f.deadline(),r.deadline())) validateDeadline(f,now);
                }
                default -> throw new IllegalArgumentException("未知任务操作");
            }
            if(stopped) stop(r,now);
            if(!action.equals("edit")) f=new Fields(f.title(),f.date(),f.deadline(),null,f.device());
            if(stopped&&f.remind()!=null) reminder=UUID.nameUUIDFromBytes(("task-reminder:"+id+":"+text(body,"requestId")).getBytes(StandardCharsets.UTF_8)).toString();
            jdbc.update("UPDATE user_todo SET title=?,task_date=?,deadline_at=?,remind_at=?,device_id=?,reminder_id=?,status=?,deleted=?,updated_at=?,version=version+1 WHERE id=? AND user_id=? AND version=?",
                f.title(),f.date(),f.deadline(),f.remind(),f.device(),reminder,status,deleted,now,id,owner,r.version());
            if(stopped&&f.remind()!=null) insertReminder(jdbc,owner,id,reminder,r.plan(),f.title(),f.device(),f.remind(),now);
            else if(reminder!=null) jdbc.update("UPDATE user_reminder SET title=?,version=version+1 WHERE id=? AND user_id=?",f.title(),reminder,owner);
            return result(view(row(owner,id,false)),null,stopped&&f.remind()==null);
        });
    }
    @Transactional
    public Map<String,Object> planAction(long owner,String id,Map<String,Object> body,long now) {
        return once(owner,"plan:"+id,body,()->{
            var p=plans.lock(owner,id);
            if(!p.taskEnabled()) throw new IllegalArgumentException("旧独立提醒计划请使用提醒接口");
            plans.action(owner,id,number(body,"version"),text(body,"action"),null,null,null,now);
            return result(null,plans.get(owner,id),false);
        });
    }
    private Task projected(ReminderPlans.Plan p,long at) {
        Long remind=p.remindOffset()==null?null:at+p.remindOffset();
        return new Task(occurrenceId(p.id(),at),p.title(),"open",0,p.taskDateEnabled()?Instant.ofEpochMilli(at).atZone(ZONE).toLocalDate().toString():null,
            p.deadlineOffset()==null?null:at+p.deadlineOffset(),remind,p.deviceId().isEmpty()?null:p.deviceId(),null,remind!=null,null,p.id(),at,true,0,0);
    }
    private static LocalDate date(Task t) { return t.taskDate()!=null?LocalDate.parse(t.taskDate()):t.deadlineAt()!=null?Instant.ofEpochMilli(t.deadlineAt()).atZone(ZONE).toLocalDate():t.remindAt()!=null?Instant.ofEpochMilli(t.remindAt()).atZone(ZONE).toLocalDate():null; }
    private static long sortTime(Task t) { return t.taskDate()!=null?LocalDate.parse(t.taskDate()).atStartOfDay(ZONE).toInstant().toEpochMilli():t.deadlineAt()!=null?t.deadlineAt():t.remindAt()!=null?t.remindAt():Long.MAX_VALUE; }
    public Map<String,Object> list(long owner,String scope,String selected,int offset,int limit,long now) {
        return list(owner,scope,selected,offset,limit,now,null);
    }
    public Map<String,Object> list(long owner,String scope,String selected,int offset,int limit,long now,String match) {
        if(!Set.of("all","today","tomorrow","upcoming","overdue","undated","completed","cancelled","expired").contains(scope)||offset<0||limit<1||limit>200) throw new IllegalArgumentException("任务查询范围无效");
        LocalDate today=Instant.ofEpochMilli(now).atZone(ZONE).toLocalDate(),target;
        try { target=selected!=null?LocalDate.parse(selected):scope.equals("tomorrow")?today.plusDays(1):today; }
        catch(DateTimeException e) { throw new IllegalArgumentException("查询日期无效"); }
        var rows=jdbc.query("SELECT * FROM user_todo WHERE user_id=?",ROW,owner);
        Map<String,Reminder> linked=new HashMap<>(); for(var r:reminders.linked(owner)) linked.put(r.id(),r);
        List<Task> values=new ArrayList<>(); Set<String> ids=new HashSet<>();
        for(Row r:rows) { ids.add(r.id()); if(!r.deleted()) {
            if(r.status().equals("open")&&r.deadline()!=null&&r.deadline()<=now)
                r=new Row(r.id(),r.owner(),r.title(),r.date(),r.deadline(),null,null,null,"expired",r.version(),r.plan(),r.occurrence(),false,r.created(),r.deadline());
            if(r.status().equals("open")||r.updated()>now-7*86400_000L) values.add(view(r,linked.get(r.reminder())));
        } }
        var templates=plans.list(owner);
        List<Task> filtered=values.stream().filter(t->{
            if(scope.equals("completed")||scope.equals("cancelled")||scope.equals("expired")) return t.status().equals(scope);
            if(!t.status().equals("open")) return false;
            LocalDate d=date(t);
            if(selected!=null||scope.equals("today")||scope.equals("tomorrow")) return d!=null&&!target.isBefore(d)&&(t.deadlineAt()==null||target.atStartOfDay(ZONE).toInstant().toEpochMilli()<t.deadlineAt());
            return switch(scope) {
                case "undated" -> d==null;
                case "overdue" -> t.deadlineAt()!=null&&t.deadlineAt()<now||t.taskDate()!=null&&LocalDate.parse(t.taskDate()).isBefore(today);
                case "upcoming" -> t.taskDate()!=null?!LocalDate.parse(t.taskDate()).isBefore(today):sortTime(t)>=now&&sortTime(t)!=Long.MAX_VALUE;
                default -> true;
            };
        }).filter(t->match==null||matchesTitle(t.title(),match)).sorted(Comparator.comparingLong(TaskStore::sortTime).thenComparing(Task::title).thenComparing(Task::id)).toList();
        int from=Math.min(offset,filtered.size()),to=Math.min(from+limit,filtered.size());
        var legacyReminders=reminders.legacy(owner);
        Map<String,Object> out=new HashMap<>(); out.put("items",filtered.subList(from,to)); out.put("total",filtered.size()); out.put("offset",offset); out.put("limit",limit); out.put("hasMore",to<filtered.size());
        out.put("serverNow",now); out.put("timeZone",ZONE.getId()); out.put("plans",templates); out.put("legacyReminders",legacyReminders);
        out.put("unansweredCount",reminders.unanswered(owner)); return out;
    }
    private static boolean matchesTitle(String title,String text) {
        if(text.contains(title)) return true;
        String noun=title.replaceFirst("^(拿|取|做|整理|完成|提交|交|喝|吃)","");
        return noun.length()>=2&&text.contains(noun);
    }
}
