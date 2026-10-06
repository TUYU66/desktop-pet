package com.xiaozhi.modules.reminder;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;

@Component
@EnableScheduling
public class ReminderScheduler {
    private static final Logger log=LoggerFactory.getLogger(ReminderScheduler.class);
    private final ReminderStore store;
    private final WebClient client;
    private final Set<String> activeDevices = ConcurrentHashMap.newKeySet();
    private final ExecutorService deliveries = new ThreadPoolExecutor(4,4,0L,TimeUnit.MILLISECONDS,
        new ArrayBlockingQueue<>(16), r -> { Thread t=new Thread(r,"reminder-delivery"); t.setDaemon(true); return t; },
        new ThreadPoolExecutor.AbortPolicy());
    @jakarta.annotation.PreDestroy
    public void shutdown() { deliveries.shutdownNow(); }
    @org.springframework.beans.factory.annotation.Autowired private ReminderPlans plans;
    @Value("${xiaozhi.python-server.base-url:http://localhost:8004}") private String baseUrl;
    @Value("${XIAOZHI_REMINDER_SERVICE_KEY:xiaozhi-reminders}") private String key;
    public ReminderScheduler(ReminderStore store,WebClient client) { this.store=store; this.client=client; }
    @Scheduled(fixedDelay=2000,initialDelay=10000)
    public void tick() {
        try {
            if(plans!=null) plans.materialize(System.currentTimeMillis());
            Map<String,List<Reminder>> batches=new LinkedHashMap<>();
            for(Reminder item:store.due(System.currentTimeMillis())) {
                var batch=batches.computeIfAbsent(item.deviceId(),ignored->new ArrayList<>());
                if(batch.size()<4) batch.add(item);
            }
            for(var entry:batches.entrySet()) {
                String device=entry.getKey(); List<Reminder> batch=List.copyOf(entry.getValue());
                if(!activeDevices.add(device)) continue;
                try {
                    deliveries.execute(() -> {
                        try { dispatchBatch(batch); }
                        catch(Exception e) { logFailure("发送",e); }
                        finally { activeDevices.remove(device); }
                    });
                } catch(RejectedExecutionException e) {
                    activeDevices.remove(device); // No claim: the next tick can retry.
                }
            }
        }
        catch(Exception e) { logFailure("调度",e); }
    }
    @Scheduled(fixedDelay=3600000,initialDelay=20000)
    public void pruneFinishedHistory() {
        try {
            int removed=store.pruneFinishedHistory(System.currentTimeMillis());
            if(removed>0) log.info("已清理超过7天的结束提醒: {} 条",removed);
        } catch(Exception e) {
            logFailure("历史清理",e);
        }
    }
    void dispatch(Reminder item) {
        dispatchBatch(List.of(item));
    }
    void dispatchBatch(List<Reminder> items) {
        if(items.isEmpty()||items.size()>4) return;
        Reminder first=items.get(0);
        if(items.stream().anyMatch(item->item.userId()!=first.userId()||!item.deviceId().equals(first.deviceId()))) return;
        try { store.requireOwner(first.userId()); }
        catch(IllegalArgumentException e) { return; }
        Map<String,Reminder> claimed=new LinkedHashMap<>();
        for(Reminder item:items) {
            String attempt=UUID.randomUUID().toString();
            try { if(store.claim(item,attempt,System.currentTimeMillis())) claimed.put(attempt,item); }
            catch(Exception e) { logFailure("认领 id="+item.id(),e); }
        }
        if(claimed.isEmpty()) return;
        List<Map<String,Object>> payload=new ArrayList<>();
        claimed.forEach((attempt,item)->payload.add(Map.of("attemptId",attempt,"text",item.title(),
            "reminderId",item.id(),"deliveryNumber",item.deliveryCount()+1)));
        boolean batch=claimed.size()>1;
        Map<String,Object> body=new HashMap<>();
        body.put("deviceId",first.deviceId());
        if(batch) { body.put("attemptId",UUID.randomUUID().toString()); body.put("items",payload); }
        else body.putAll(payload.get(0));
        String fallback="unknown";
        Map<?,?> outcomes=Map.of();
        try {
            Map<?,?> response=client.post().uri(baseUrl+"/xiaozhi/reminders/announce")
                .header("Service-Key",key.isBlank()?"xiaozhi-reminders":key).bodyValue(body)
                .exchangeToMono(r->r.bodyToMono(Map.class).filter(ignored->r.statusCode().is2xxSuccessful())).block(Duration.ofSeconds(150));
            if(response!=null&&response.get("code") instanceof Number n&&n.intValue()==0) {
                if(batch&&response.get("outcomes") instanceof Map<?,?> perItem) outcomes=perItem;
                else if(!batch&&response.get("outcome") instanceof String o&&Set.of("sent","retry","unknown").contains(o)) fallback=o;
            }
        } catch(Exception e) {
            Throwable cause=e;
            while(cause.getCause()!=null&&cause.getCause()!=cause) cause=cause.getCause();
            if(cause instanceof java.net.ConnectException||cause instanceof java.net.UnknownHostException) {
                fallback="retry";
            }
            logFailure("发送未确认 device="+first.deviceId(),e);
        }
        for(var entry:claimed.entrySet()) {
            String outcome=outcomes.get(entry.getKey()) instanceof String o&&Set.of("sent","retry","unknown").contains(o)?o:fallback;
            String error=switch(outcome) { case "sent"->null; case "retry"->"设备离线、忙碌或语音暂不可用，等待重试"; default->"发送结果未确认，请核实后再操作"; };
            // A partial batch keeps an independent receipt and retry counter per reminder.
            try { store.finish(entry.getValue(),entry.getKey(),outcome,error,System.currentTimeMillis()); }
            catch(Exception e) { logFailure("保存回执 id="+entry.getValue().id(),e); }
        }
    }
    private static void logFailure(String phase,Exception error) {
        Throwable cause=error;
        while(cause.getCause()!=null&&cause.getCause()!=cause) cause=cause.getCause();
        if(cause instanceof java.sql.SQLException sql)
            log.warn("提醒{}暂不可用: {}, root={}, SQLState={}, vendorCode={}",phase,error.getClass().getSimpleName(),cause.getClass().getSimpleName(),sql.getSQLState(),sql.getErrorCode());
        else log.warn("提醒{}暂不可用: {}, root={}",phase,error.getClass().getSimpleName(),cause.getClass().getSimpleName());
    }
}
