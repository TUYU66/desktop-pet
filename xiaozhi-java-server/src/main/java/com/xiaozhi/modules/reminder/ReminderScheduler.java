package com.xiaozhi.modules.reminder;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import reactor.core.Disposable;
import reactor.core.Disposables;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;

@Component
@EnableScheduling
public class ReminderScheduler {
    private static final Logger log = LoggerFactory.getLogger(ReminderScheduler.class);
    private final ReminderStore store;
    private final WebClient client;
    private final Semaphore slots = new Semaphore(4);
    private final Map<String, Disposable.Swap> active = new ConcurrentHashMap<>();
    @org.springframework.beans.factory.annotation.Autowired private ReminderPlans plans;
    @Value("${xiaozhi.python-server.base-url:http://localhost:8004}") private String baseUrl;
    @Value("${XIAOZHI_REMINDER_SERVICE_KEY:}") private String key;

    public ReminderScheduler(ReminderStore store, WebClient client) {
        this.store = store;
        this.client = client;
    }

    @jakarta.annotation.PreDestroy
    public void shutdown() {
        active.values().forEach(Disposable::dispose);
        active.clear();
    }

    @Scheduled(fixedDelay = 2000, initialDelay = 10000)
    public void tick() {
        try {
            long now = System.currentTimeMillis();
            if (plans != null) plans.materialize(now);
            Map<String, List<Reminder>> batches = new LinkedHashMap<>();
            for (Reminder item : store.due(now)) {
                var batch = batches.computeIfAbsent(item.deviceId(), ignored -> new ArrayList<>());
                if (batch.size() < 4) batch.add(item);
            }
            for (var entry : batches.entrySet()) {
                String device = entry.getKey();
                var ticket = Disposables.swap();
                if (active.putIfAbsent(device, ticket) != null) continue;
                if (!slots.tryAcquire()) {
                    active.remove(device, ticket);
                    continue; // No claim or queued work: a later tick may retry.
                }
                ticket.update(dispatchBatch(List.copyOf(entry.getValue()))
                    .doFinally(signal -> {
                        active.remove(device, ticket);
                        slots.release();
                    }).subscribe(ignored -> {}, error -> logFailure("发送", error)));
            }
        } catch (Exception error) {
            logFailure("调度", error);
        }
    }

    @Scheduled(fixedDelay = 3600000, initialDelay = 20000)
    public void pruneFinishedHistory() {
        try {
            int removed = store.pruneFinishedHistory(System.currentTimeMillis());
            if (removed > 0) log.info("已清理超过7天的结束提醒: {} 条", removed);
        } catch (Exception error) {
            logFailure("历史清理", error);
        }
    }

    Mono<Void> dispatch(Reminder item) {
        return dispatchBatch(List.of(item));
    }

    private record Reply(Map<?, ?> body, String fallback) {}

    Mono<Void> dispatchBatch(List<Reminder> items) {
        return Mono.defer(() -> prepareDelivery(items)).subscribeOn(Schedulers.boundedElastic());
    }

    private Mono<Void> prepareDelivery(List<Reminder> items) {
        if (items.isEmpty() || items.size() > 4) return Mono.empty();
        Reminder first = items.get(0);
        if (items.stream().anyMatch(item -> item.userId() != first.userId()
            || !item.deviceId().equals(first.deviceId()))) return Mono.empty();
        try {
            store.requireOwner(first.userId());
        } catch (IllegalArgumentException error) {
            return Mono.empty();
        }
        Map<String, Reminder> claimed = new LinkedHashMap<>();
        for (Reminder item : items) {
            String attempt = UUID.randomUUID().toString();
            try {
                if (store.claim(item, attempt, System.currentTimeMillis())) claimed.put(attempt, item);
            } catch (Exception error) {
                logFailure("认领 id=" + item.id(), error);
            }
        }
        if (claimed.isEmpty()) return Mono.empty();
        List<Map<String, Object>> payload = new ArrayList<>();
        claimed.forEach((attempt, item) -> payload.add(Map.of(
            "attemptId", attempt, "text", item.title(), "reminderId", item.id(),
            "deliveryNumber", item.status().equals("awaiting_confirmation")
                ? item.deliveryCount() : item.deliveryCount() + 1)));
        boolean batch = claimed.size() > 1;
        Map<String, Object> body = new HashMap<>();
        body.put("deviceId", first.deviceId());
        if (batch) {
            body.put("attemptId", UUID.randomUUID().toString());
            body.put("items", payload);
        } else body.putAll(payload.get(0));

        return client.post().uri(baseUrl + "/xiaozhi/reminders/announce")
            .header("Service-Key", key).bodyValue(body)
            .exchangeToMono(response -> response.bodyToMono(Map.class)
                .filter(ignored -> response.statusCode().is2xxSuccessful()))
            .map(response -> new Reply(response, "unknown"))
            .defaultIfEmpty(new Reply(Map.of(), "unknown"))
            .timeout(Duration.ofSeconds(150))
            .onErrorResume(error -> {
                Throwable cause = rootCause(error);
                String fallback = cause instanceof java.net.ConnectException
                    || cause instanceof java.net.UnknownHostException ? "retry" : "unknown";
                logFailure("发送未确认 device=" + first.deviceId(), error);
                return Mono.just(new Reply(Map.of(), fallback));
            })
            // JDBC receipts must not execute on the HTTP event loop.
            .publishOn(Schedulers.boundedElastic())
            .doOnNext(reply -> finish(claimed, batch, reply)).then();
    }

    private void finish(Map<String, Reminder> claimed, boolean batch, Reply reply) {
        String fallback = reply.fallback();
        Map<?, ?> outcomes = Map.of();
        if (reply.body().get("code") instanceof Number n && n.intValue() == 0) {
            if (batch && reply.body().get("outcomes") instanceof Map<?, ?> results) outcomes = results;
            else if (!batch && validOutcome(reply.body().get("outcome"))) fallback = (String) reply.body().get("outcome");
        }
        for (var entry : claimed.entrySet()) {
            Object result = outcomes.get(entry.getKey());
            String outcome = validOutcome(result) ? (String) result : fallback;
            String error = switch (outcome) {
                case "sent" -> null;
                case "retry" -> "设备离线、忙碌或语音暂不可用，等待重试";
                default -> "发送结果未确认，请核实后再操作";
            };
            try {
                store.finish(entry.getValue(), entry.getKey(), outcome, error, System.currentTimeMillis());
            } catch (Exception failure) {
                logFailure("保存回执 id=" + entry.getValue().id(), failure);
            }
        }
    }

    private static boolean validOutcome(Object value) {
        return value instanceof String text && Set.of("sent", "retry", "unknown").contains(text);
    }

    private static Throwable rootCause(Throwable error) {
        while (error.getCause() != null && error.getCause() != error) error = error.getCause();
        return error;
    }

    private static void logFailure(String phase, Throwable error) {
        Throwable cause = rootCause(error);
        if (cause instanceof java.sql.SQLException sql)
            log.warn("提醒{}暂不可用: {}, root={}, SQLState={}, vendorCode={}", phase,
                error.getClass().getSimpleName(), cause.getClass().getSimpleName(), sql.getSQLState(), sql.getErrorCode());
        else log.warn("提醒{}暂不可用: {}, root={}", phase,
            error.getClass().getSimpleName(), cause.getClass().getSimpleName());
    }
}
