package com.xiaozhi.modules.reminder;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.security.config.CurrentUser;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;
import java.util.*;
import java.util.concurrent.Callable;

@RestController
@RequestMapping("/xiaozhi/api/reminders")
public class ReminderController {
    private final ReminderStore store;
    @org.springframework.beans.factory.annotation.Autowired private ReminderPlans plans;
    @org.springframework.beans.factory.annotation.Autowired private ReminderCreations creations;
    public ReminderController(ReminderStore store) { this.store=store; }
    private <T> Mono<Result<T>> run(UserEntity user,Callable<T> task) {
        if(user==null) return Mono.just(Result.error(401,"未登录"));
        return Mono.fromCallable(()->{ store.requireOwner(user.getId()); return Result.ok(task.call()); })
            .subscribeOn(Schedulers.boundedElastic())
            .onErrorResume(ReminderStore.Conflict.class,e->Mono.just(Result.error(409,e.getMessage())))
            .onErrorResume(IllegalArgumentException.class,e->Mono.just(Result.error(400,e.getMessage())));
    }
    @GetMapping public Mono<Result<Map<String,Object>>> list(@CurrentUser UserEntity user) {
        return run(user,()->Map.of("items",store.list(user.getId()),"plans",plans.list(user.getId()),"serverNow",System.currentTimeMillis(),"unansweredCount",store.unanswered(user.getId()),"itemTotal",store.visibleCount(user.getId(),null)));
    }
    @GetMapping("/devices") public Mono<Result<List<Map<String,Object>>>> devices(@CurrentUser UserEntity user) {
        return run(user,()->store.devices(user.getId()));
    }
    @GetMapping("/settings") public Mono<Result<Map<String,Object>>> settings(@CurrentUser UserEntity user) {
        return run(user,()->store.musicSettings(user.getId()));
    }
    @PutMapping("/settings") public Mono<Result<Map<String,Object>>> settings(@CurrentUser UserEntity user,@RequestBody Map<String,Object> body) {
        return run(user,()->store.updateMusicSettings(user.getId(),body));
    }
    @PostMapping public Mono<Result<Object>> create(@CurrentUser UserEntity user,@RequestBody Map<String,Object> body) {
        return run(user,()->createBody(user.getId(),body));
    }
    private Object createBody(long owner,Map<String,Object> body) {
        String id=string(body,"requestId"),device=string(body,"deviceId"),title=string(body,"title").trim();
        long now=System.currentTimeMillis();
        if(body.containsKey("triggerAt")) {
            long at=number(body,"triggerAt");
            String recurrence=body.getOrDefault("recurrence","once").toString();
            return creations.create(owner,id,device,title,at,recurrence,null,now);
        }
        return creations.create(owner,id,device,title,null,"once",number(body,"seconds"),now);
    }
    @PostMapping("/{id}/actions") public Mono<Result<Reminder>> act(@CurrentUser UserEntity user,@PathVariable String id,@RequestBody Map<String,Object> body) {
        return run(user,()->actionBody(user.getId(),id,body));
    }
    @DeleteMapping("/{id}") public Mono<Result<String>> deleteHistory(@CurrentUser UserEntity user,@PathVariable String id,@RequestParam long version) {
        return run(user,()->{ store.deleteHistory(user.getId(),id,version,System.currentTimeMillis()); return "记录已删除"; });
    }
    private Reminder actionBody(long owner,String id,Map<String,Object> body) {
        if(body.containsKey("triggerAt")&&!Set.of("edit","snooze").contains(string(body,"action"))) throw new IllegalArgumentException("此操作不能修改提醒时间");
        if(body.containsKey("triggerAt")) return store.reschedule(owner,id,number(body,"version"),body.containsKey("title")?string(body,"title"):store.get(owner,id).title(),number(body,"triggerAt"),System.currentTimeMillis());
        return store.act(owner,id,number(body,"version"),string(body,"action"),body.containsKey("seconds")?number(body,"seconds"):300,System.currentTimeMillis());
    }
    @PostMapping("/plans/{id}/actions") public Mono<Result<String>> planAction(@CurrentUser UserEntity user,@PathVariable String id,@RequestBody Map<String,Object> body) {
        return run(user,()->{ plans.action(user.getId(),id,number(body,"version"),string(body,"action"),body.containsKey("triggerAt")?number(body,"triggerAt"):null,(String)body.get("title"),(String)body.get("recurrence"),System.currentTimeMillis()); return "已更新，已生成的本次提醒保留"; });
    }
    @GetMapping("/internal/catchup") public Mono<Result<Map<String,Object>>> catchup(@CurrentUser UserEntity user,@RequestParam String deviceId) {
        return run(user,()->{
            long now=System.currentTimeMillis();
            store.refreshCatchup(user.getId(),deviceId,now);
            return Map.of("items",store.catchupItems(user.getId(),deviceId,now),
                "count",store.catchupCount(user.getId(),deviceId,now),"serverNow",now);
        });
    }
    @GetMapping("/internal/state") public Mono<Result<Map<String,Object>>> internalState(@CurrentUser UserEntity user,@RequestParam String deviceId) {
        return run(user,()->{
            Map<String,Object> result=new HashMap<>(store.musicSettings(user.getId()));
            result.put("items",store.forDevice(user.getId(),deviceId));
            result.put("plans",plans.list(user.getId()).stream().filter(p->p.deviceId().equals(deviceId)).toList());
            result.put("serverNow",System.currentTimeMillis());
            result.put("itemTotal",store.visibleCount(user.getId(),deviceId));
            result.put("unansweredCount",store.unansweredForDevice(user.getId(),deviceId));
            return result;
        });
    }
    @PostMapping("/internal/commands") public Mono<Result<Object>> command(@CurrentUser UserEntity user,@RequestBody Map<String,Object> body) {
        return run(user,()->{
            String action=string(body,"action"),device=string(body,"deviceId");
            if(action.equals("create")) return createBody(user.getId(),body);
            String id=string(body,"id");
            if(action.startsWith("plan_")) {
                var p=plans.list(user.getId()).stream().filter(it->it.id().equals(id)&&it.deviceId().equals(device)).findFirst().orElseThrow(()->new IllegalArgumentException("计划不存在"));
                if(action.equals("plan_to_once")) return Map.of("item",plans.toOnce(user.getId(),id,device,number(body,"version"),number(body,"triggerAt"),System.currentTimeMillis()),"convertedFrom",id);
                if(action.equals("plan_occurrence_edit")) return Map.of("item",plans.editOccurrence(user.getId(),id,device,number(body,"version"),number(body,"occurrenceAt"),number(body,"triggerAt"),System.currentTimeMillis()),"sourcePlan",id);
                plans.action(user.getId(),id,number(body,"version"),action.substring(5),body.containsKey("triggerAt")?number(body,"triggerAt"):null,(String)body.get("title"),(String)body.get("recurrence"),System.currentTimeMillis());
                return Map.of("id",p.id());
            }
            var item=store.get(user.getId(),id);
            if(!item.deviceId().equals(device)) throw new IllegalArgumentException("设备与提醒不匹配");
            if(action.equals("followup_claim")) return Map.of("claimed",store.claimFollowup(user.getId(),device,id,string(body,"token"),number(body,"stage"),System.currentTimeMillis()));
            if(action.equals("followup_valid")) return Map.of("valid",body.containsKey("stage")
                ?store.followupValid(user.getId(),device,id,string(body,"token"),number(body,"stage"),System.currentTimeMillis())
                :store.followupValid(user.getId(),device,id,string(body,"token"),System.currentTimeMillis()));
            if(action.equals("followup_finish")) return Map.of("finished",store.finishFollowup(user.getId(),device,id,string(body,"token"),number(body,"stage"),System.currentTimeMillis()));
            if(action.equals("followup_release")) { store.releaseFollowup(user.getId(),device,id,string(body,"token")); return Map.of("released",true); }
            if(action.equals("listening")) { store.listening(user.getId(),id,string(body,"attemptId"),System.currentTimeMillis()); return Map.of("id",id); }
            return actionBody(user.getId(),id,body);
        });
    }
    private static String string(Map<String,Object> body,String key) {
        if(!(body.get(key) instanceof String s)||s.isBlank()||s.length()>120) throw new IllegalArgumentException("字段无效："+key);
        return s;
    }
    private static long number(Map<String,Object> body,String key) {
        if(!(body.get(key) instanceof Number n)||!Double.isFinite(n.doubleValue())||n.longValue()<0||n.doubleValue()!=n.longValue()) throw new IllegalArgumentException("字段必须为非负整数："+key);
        return n.longValue();
    }
}
