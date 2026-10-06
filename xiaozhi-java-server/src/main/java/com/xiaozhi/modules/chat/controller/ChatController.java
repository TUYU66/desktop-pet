package com.xiaozhi.modules.chat.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.chat.dao.ChatHistoryDao;
import com.xiaozhi.modules.chat.dao.ChatTitleDao;
import com.xiaozhi.modules.chat.entity.ChatHistoryEntity;
import com.xiaozhi.modules.chat.entity.ChatTitleEntity;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import java.time.Duration;
import java.time.LocalDate;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/xiaozhi/api/chat")
public class ChatController {

    @Resource private ChatTitleDao titleDao;
    @Resource private ChatHistoryDao historyDao;
    @Resource private WebClient webClient;
    @Resource private com.xiaozhi.modules.chat.service.ChatHistoryPages historyPages;

    @Value("${xiaozhi.python-server.base-url:http://localhost:8004}")
    private String pythonBaseUrl;
    @Value("${XIAOZHI_DEVICE_SERVICE_KEY:xiaozhi-device}") private String deviceServiceKey;

    /** 获取会话列表，可按 roleId 筛选 */
    @GetMapping("/sessions")
    public Result<List<ChatTitleEntity>> allSessions(
            @RequestParam(required = false) String roleId) {
        LambdaQueryWrapper<ChatTitleEntity> wrapper = new LambdaQueryWrapper<ChatTitleEntity>()
                .orderByDesc(ChatTitleEntity::getCreateDate);
        if (roleId != null && !roleId.isEmpty()) {
            wrapper.eq(ChatTitleEntity::getRoleId, roleId);
        }
        return Result.ok(titleDao.selectList(wrapper));
    }

    /** 获取某会话的消息 */
    @GetMapping("/sessions/{sessionId}/messages")
    public Result<List<ChatHistoryEntity>> messages(@PathVariable String sessionId) {
        return Result.ok(historyDao.selectList(
                new LambdaQueryWrapper<ChatHistoryEntity>()
                        .eq(ChatHistoryEntity::getSessionId, sessionId)
                        .orderByAsc(ChatHistoryEntity::getCreateDate)));
    }

    /** 按时间与消息编号分页，返回顺序始终为从旧到新。 */
    @GetMapping("/sessions/{sessionId}/message-page")
    public Result<Map<String,Object>> messagePage(@PathVariable String sessionId,
            @RequestParam(required=false) String beforeCursor,@RequestParam(required=false) String afterCursor,
            @RequestParam(required=false) @org.springframework.format.annotation.DateTimeFormat(iso=org.springframework.format.annotation.DateTimeFormat.ISO.DATE) LocalDate date,
            @RequestParam(defaultValue="50") int limit) {
        try { return Result.ok(historyPages.page(sessionId,beforeCursor,afterCursor,date,limit)); }
        catch(IllegalArgumentException e) { return Result.error(400,e.getMessage()); }
    }
    @GetMapping("/sessions/{sessionId}/message-dates")
    public Result<Map<String,Object>> messageDates(@PathVariable String sessionId) {
        return Result.ok(historyPages.dates(sessionId));
    }

    /** 指定日期只删除当天消息；省略日期删除整个会话。 */
    @DeleteMapping("/sessions/{sessionId}")
    @org.springframework.transaction.annotation.Transactional
    public Result<?> deleteSession(@PathVariable String sessionId,
            @RequestParam(required = false) @org.springframework.format.annotation.DateTimeFormat(iso = org.springframework.format.annotation.DateTimeFormat.ISO.DATE) LocalDate date) {
        LambdaQueryWrapper<ChatHistoryEntity> query = new LambdaQueryWrapper<ChatHistoryEntity>()
                .eq(ChatHistoryEntity::getSessionId, sessionId);
        if (date != null) {
            query.ge(ChatHistoryEntity::getCreateDate, date.atStartOfDay())
                 .lt(ChatHistoryEntity::getCreateDate, date.plusDays(1).atStartOfDay());
        } else {
            titleDao.delete(new LambdaQueryWrapper<ChatTitleEntity>().eq(ChatTitleEntity::getSessionId, sessionId));
        }
        historyDao.delete(query);
        return Result.ok();
    }

    /** 新建空会话 */
    @PostMapping("/new")
    public Result<Map<String, String>> newSession(@RequestBody Map<String, String> body) {
        String roleId = body.get("roleId");
        String roleName = body.get("roleName");

        // 如果该角色已有 session，直接返回已有的
        if (roleId != null && !roleId.isEmpty()) {
            List<ChatTitleEntity> existing = titleDao.selectList(
                    new LambdaQueryWrapper<ChatTitleEntity>()
                            .eq(ChatTitleEntity::getRoleId, roleId)
                            .last("LIMIT 1"));
            if (!existing.isEmpty()) {
                Map<String, String> result = new HashMap<>();
                result.put("sessionId", existing.get(0).getSessionId());
                return Result.ok(result);
            }
        }

        String sessionId = UUID.randomUUID().toString();
        ChatTitleEntity t = new ChatTitleEntity();
        t.setSessionId(sessionId);
        t.setTitle("新对话");
        t.setRoleId(roleId);
        t.setRoleName(roleName == null || roleName.isBlank() ? "桌面宠物" : roleName);
        t.setCreateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
        t.setUpdateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
        titleDao.insert(t);

        Map<String, String> result = new HashMap<>();
        result.put("sessionId", sessionId);
        return Result.ok(result);
    }

    /** 按角色获取/创建唯一会话 */
    @GetMapping("/session-by-role/{roleId}")
    public Result<Map<String, String>> getSessionByRole(
            @PathVariable String roleId,
            @RequestParam(defaultValue = "") String roleName) {
        // 查找该角色已有的 session
        List<ChatTitleEntity> existing = titleDao.selectList(
                new LambdaQueryWrapper<ChatTitleEntity>()
                        .eq(ChatTitleEntity::getRoleId, roleId)
                        .last("LIMIT 1"));
        if (!existing.isEmpty()) {
            Map<String, String> result = new HashMap<>();
            result.put("sessionId", existing.get(0).getSessionId());
            return Result.ok(result);
        }
        // 不存在则创建
        String sessionId = UUID.randomUUID().toString();
        ChatTitleEntity t = new ChatTitleEntity();
        t.setSessionId(sessionId);
        t.setTitle("新对话");
        t.setRoleId(roleId);
        t.setRoleName(roleName == null || roleName.isBlank() ? "桌面宠物" : roleName);
        t.setCreateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
        t.setUpdateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
        titleDao.insert(t);

        Map<String, String> result = new HashMap<>();
        result.put("sessionId", sessionId);
        return Result.ok(result);
    }

    /**
     * 前端文字输入 → Java 转发到 Python
     * Python 端找到对应设备 handler，调用 chat(text)，走 LLM→TTS→ESP32 音频播放
     */
    @PostMapping("/send")
    public Mono<Result<Object>> sendMessage(@RequestBody Map<String, String> body) {
        String text = body.get("text");
        String deviceId = body.get("deviceId");
        String sessionId = body.get("sessionId");
        if (text == null || text.isBlank()) {
            return Mono.just(Result.error("text 不能为空"));
        }
        Map<String, String> payload = new HashMap<>();
        payload.put("text", text.trim());
        if (deviceId != null) payload.put("deviceId", deviceId);
        if (sessionId != null) payload.put("sessionId", sessionId);
        if (body.containsKey("requestId")) payload.put("requestId", body.get("requestId"));
        return webClient.post()
                    .uri(pythonBaseUrl + "/xiaozhi/chat/send")
                    .bodyValue(payload)
                    .exchangeToMono(response -> response.bodyToMono(Map.class)
                            .map(result -> {
                                Object code = result.get("code");
                                if (response.statusCode().is2xxSuccessful()
                                        && code instanceof Number && ((Number) code).intValue() == 0) {
                                    return Result.<Object>ok(String.valueOf(result.getOrDefault("msg", "消息已提交")), result.get("data"));
                                }
                                return Result.<Object>error(code instanceof Number ? ((Number)code).intValue() : 500, String.valueOf(result.getOrDefault("msg", "消息提交失败")));
                            }))
                    .switchIfEmpty(Mono.just(Result.error("语音服务返回空响应")))
                    .timeout(Duration.ofSeconds(20))
                    .onErrorReturn(Result.error("无法连接语音服务或请求超时，请检查 Python 服务"));
    }

    @GetMapping("/requests/{requestId}")
    public Mono<Result<Object>> requestStatus(@PathVariable String requestId) {
        if(!requestId.matches("[A-Za-z0-9_-]{1,128}")) return Mono.just(Result.error("请求编号无效"));
        return webClient.get().uri(pythonBaseUrl+"/xiaozhi/chat/requests/{id}",requestId)
            .exchangeToMono(response->response.bodyToMono(Map.class).map(body->{
                Object code=body.get("code");
                if(response.statusCode().is2xxSuccessful()&&code instanceof Number n&&n.intValue()==0)
                    return Result.<Object>ok(body.get("data"));
                return Result.<Object>error(code instanceof Number n?n.intValue():500,String.valueOf(body.getOrDefault("msg","结果待核实")));
            })).switchIfEmpty(Mono.just(Result.error("查询无响应，结果待核实")))
            .timeout(Duration.ofSeconds(5)).onErrorReturn(Result.error("查询失败，结果待核实；请勿自动重发"));
    }

    /** 获取在线设备的活跃会话ID（转发到 Python） */
    @GetMapping("/active-sessions")
    public Mono<Result<List<String>>> activeSessions() {
        return webClient.get()
                    .uri(pythonBaseUrl + "/xiaozhi/device/list")
                    .header("Service-Key",deviceServiceKey.isBlank()?"xiaozhi-device":deviceServiceKey)
                    .retrieve()
                    .bodyToMono(Map.class)
                    .map(resp -> {
            if (resp != null && resp.get("data") instanceof List) {
                List<Map<String, Object>> devices = (List) resp.get("data");
                List<String> active = devices.stream()
                        .map(d -> (String) d.get("sessionId"))
                        .filter(s -> s != null && !s.isEmpty())
                        .collect(java.util.stream.Collectors.toList());
                return Result.ok(active);
            }
                        return Result.<List<String>>error("设备状态响应格式错误");
                    })
                    .switchIfEmpty(Mono.just(Result.error("设备状态响应为空")))
                    .timeout(Duration.ofSeconds(5))
                    .onErrorReturn(Result.error("无法查询设备状态，请检查 Python 服务"));
    }

    /** 清除指定角色的记忆（转发到 Python） */
    @DeleteMapping("/memory/{roleId}")
    public Result<?> clearMemory(@PathVariable String roleId) {
        try {
            webClient.delete()
                    .uri(pythonBaseUrl + "/xiaozhi/memory/" + roleId)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block(java.time.Duration.ofSeconds(10));
            return Result.ok();
        } catch (Exception e) {
            return Result.error("清除记忆失败: " + e.getMessage());
        }
    }

    /** 保存会话记忆摘要（Python 调用 → 存入数据库） */
    @PostMapping("/memory/{sessionId}")
    public Result<?> saveMemory(@PathVariable String sessionId, @RequestBody Map<String, String> body) {
        String summary = body.get("summary");
        if (sessionId == null || summary == null) return Result.error("参数不足");

        ChatTitleEntity existing = titleDao.selectOne(
                new LambdaQueryWrapper<ChatTitleEntity>().eq(ChatTitleEntity::getSessionId, sessionId));
        if (existing != null) {
            existing.setMemorySummary(summary);
            existing.setUpdateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
            titleDao.updateById(existing);
        } else {
            // 防止竞态：title 尚未创建，先创建再存记忆
            ChatTitleEntity t = new ChatTitleEntity();
            t.setSessionId(sessionId);
            t.setTitle("新对话");
            t.setMemorySummary(summary);
            t.setCreateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
            t.setUpdateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
            titleDao.insert(t);
        }
        return Result.ok();
    }

    /** 读取会话记忆摘要（Python 调用 → 从数据库读） */
    @GetMapping("/sessions/{sessionId}/memory")
    public Result<String> getMemory(@PathVariable String sessionId) {
        ChatTitleEntity existing = titleDao.selectOne(
                new LambdaQueryWrapper<ChatTitleEntity>().eq(ChatTitleEntity::getSessionId, sessionId));
        if (existing != null && existing.getMemorySummary() != null && !existing.getMemorySummary().isEmpty()) {
            return Result.ok(existing.getMemorySummary());
        }
        return Result.ok("");
    }

    /** 聊天记录上报（Python Server 调用，无需认证） */
    @PostMapping("/report")
    public Result<?> report(@RequestBody Map<String, Object> body) {
        String sessionId = (String) body.get("sessionId");
        Object chatTypeObj = body.get("chatType");
        String content = (String) body.get("content");
        if (sessionId == null || chatTypeObj == null || content == null) {
            return Result.error("参数不足");
        }
        String chatType = chatTypeObj.toString();

        ChatHistoryEntity msg = new ChatHistoryEntity();
        msg.setSessionId(sessionId);
        msg.setChatType(chatType);
        msg.setContent(content);
        String deviceId = (String) body.get("deviceId");
        if (deviceId != null) {
            try {
                msg.setDeviceId(Long.valueOf(deviceId));
            } catch (NumberFormatException ignored) {
                // deviceId 可能是 MAC 地址，不是数字，忽略
            }
        }
        msg.setCreateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
        historyDao.insert(msg);

        return Result.ok();
    }

    /** 自动生成/更新会话标题（Python Server 调用） */
    @PostMapping("/title/generate")
    public Result<?> generateTitle(@RequestBody Map<String, Object> body) {
        String sessionId = (String) body.get("sessionId");
        String title = (String) body.get("title");
        String roleId = (String) body.get("roleId");
        String roleName = (String) body.get("roleName");
        if (sessionId == null || title == null) return Result.error("参数不足");

        ChatTitleEntity existing = titleDao.selectOne(
                new LambdaQueryWrapper<ChatTitleEntity>().eq(ChatTitleEntity::getSessionId, sessionId));
        if (existing != null) {
            existing.setTitle(title);
            existing.setUpdateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
            if (roleId != null) existing.setRoleId(roleId);
            if (roleName != null && !roleName.isBlank()) existing.setRoleName(roleName);
            titleDao.updateById(existing);
        } else {
            ChatTitleEntity t = new ChatTitleEntity();
            t.setSessionId(sessionId);
            t.setTitle(title);
            if (roleId != null) t.setRoleId(roleId);
            t.setRoleName(roleName == null || roleName.isBlank() ? "桌面宠物" : roleName);
            t.setCreateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
            t.setUpdateDate(LocalDateTime.now(ZoneId.of("Asia/Shanghai")));
            titleDao.insert(t);
        }
        return Result.ok();
    }
}
