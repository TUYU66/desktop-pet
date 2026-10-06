package com.xiaozhi.modules.user.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.security.config.CurrentUser;
import com.xiaozhi.modules.user.entity.BotConfigEntity;
import com.xiaozhi.modules.user.entity.UserEntity;
import com.xiaozhi.modules.user.dao.BotConfigDao;
import com.xiaozhi.modules.user.service.UserService;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.LocalDateTime;
import java.time.Duration;
import reactor.core.publisher.Mono;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/xiaozhi/api/user")
public class UserController {

    @Resource
    private UserService userService;

    @Resource
    private BotConfigDao botConfigDao;

    @Resource
    private WebClient webClient;

    @Value("${xiaozhi.python-server.base-url:http://localhost:8004}")
    private String pythonBaseUrl;
    @Value("${XIAOZHI_DEVICE_SERVICE_KEY:xiaozhi-device}") private String deviceServiceKey;

    @GetMapping("/info")
    public Result<Map<String, Object>> info(@CurrentUser UserEntity user) {
        if (user == null) return Result.error(401, "未登录");
        Map<String, Object> data = new HashMap<>();
        data.put("id", user.getId());
        data.put("username", user.getUsername());
        data.put("email", user.getEmail());
        data.put("mobile", user.getMobile());
        data.put("avatar", user.getAvatar());
        data.put("nickname", user.getNickname());
        data.put("superAdmin", user.getSuperAdmin());
        return Result.ok(data);
    }

    @PutMapping("/password")
    public Result<?> updatePassword(@CurrentUser UserEntity user, @RequestBody Map<String, String> params) {
        if (user == null) return Result.error(401, "未登录");
        String currentPassword = params.get("currentPassword");
        String newPassword = params.get("newPassword");
        if (currentPassword == null || newPassword == null) {
            return Result.error("当前密码和新密码不能为空");
        }
        userService.updatePassword(user.getId(), currentPassword, newPassword);
        return Result.ok();
    }

    /** 获取机器人配置（prompt / wakeWords） */
    @GetMapping("/config")
    public Result<Map<String, String>> getConfig(@CurrentUser UserEntity user) {
        if (user == null) return Result.error(401, "未登录");
        List<BotConfigEntity> list = botConfigDao.selectList(
                new LambdaQueryWrapper<BotConfigEntity>().eq(BotConfigEntity::getUserId, user.getId()));
        Map<String, String> config = list.stream()
                .collect(Collectors.toMap(BotConfigEntity::getConfigKey, BotConfigEntity::getConfigValue));
        // 如果该用户还没有任何配置，自动初始化默认角色
        if (config.isEmpty()) {
            initDefaultConfig(user.getId());
            // 重新查一次
            list = botConfigDao.selectList(
                    new LambdaQueryWrapper<BotConfigEntity>().eq(BotConfigEntity::getUserId, user.getId()));
            config = list.stream()
                    .collect(Collectors.toMap(BotConfigEntity::getConfigKey, BotConfigEntity::getConfigValue));
        }
        return Result.ok(config);
    }

    private void initDefaultConfig(Long userId) {
        LocalDateTime now = LocalDateTime.now();
        String roles = "[{\"id\": \"default\", \"name\": \"小智\", \"prompt\": \"你是小智，一个温和自然的桌面陪伴机器人。认真倾听用户的日常分享，表达简洁，不编造事实或任务执行结果。\", \"wakeWords\": \"你好小智\", \"voice\": \"zh-CN-XiaoxiaoNeural\"}]";
        String[] keys = {"roles", "activeRole", "prompt", "wakeWords", "voice"};
        String[] values = {roles, "default", "你是小智，一个温和自然的桌面陪伴机器人。认真倾听用户的日常分享，表达简洁，不编造事实或任务执行结果。", "你好小智", "zh-CN-XiaoxiaoNeural"};
        for (int i = 0; i < keys.length; i++) {
            BotConfigEntity cfg = new BotConfigEntity();
            cfg.setUserId(userId);
            cfg.setConfigKey(keys[i]);
            cfg.setConfigValue(values[i]);
            cfg.setCreateDate(now);
            cfg.setUpdateDate(now);
            botConfigDao.insert(cfg);
        }
    }

    /** 保存机器人配置 */
    @PutMapping("/config")
    public Result<?> saveConfig(@CurrentUser UserEntity user, @RequestBody Map<String, String> body) {
        if (user == null) return Result.error(401, "未登录");
        if (body.containsKey("customWakeWord")) {
            String word = body.get("customWakeWord");
            if (word == null || (!word.isEmpty() && !word.matches("[\\u4e00-\\u9fff]{3,8}"))) {
                return Result.error("唤醒词请填写 3～8 个汉字，或留空关闭");
            }
            if (!body.containsKey("customWakePinyin")) body.put("customWakePinyin", "");
        }
        if (body.containsKey("customWakePinyin")) {
            String pinyin=body.get("customWakePinyin"), word=body.get("customWakeWord");
            if(pinyin==null || word==null || (!pinyin.isEmpty() &&
                (pinyin.length()>63 || !pinyin.matches("[a-z]+(?: [a-z]+)*") || pinyin.split(" ").length!=word.length())))
                return Result.error("读音请与唤醒词一起保存，每字填写一个无声调拼音，以空格分隔");
        }
        // 一次性查出该用户所有配置
        List<BotConfigEntity> existingList = botConfigDao.selectList(
                new LambdaQueryWrapper<BotConfigEntity>().eq(BotConfigEntity::getUserId, user.getId()));
        Map<String, BotConfigEntity> existingMap = existingList.stream()
                .collect(Collectors.toMap(BotConfigEntity::getConfigKey, e -> e));

        LocalDateTime now = LocalDateTime.now();
        for (Map.Entry<String, String> entry : body.entrySet()) {
            String key = entry.getKey();
            String value = entry.getValue();
            BotConfigEntity existing = existingMap.get(key);
            if (existing != null) {
                existing.setConfigValue(value);
                existing.setUpdateDate(now);
                botConfigDao.updateById(existing);
            } else {
                BotConfigEntity cfg = new BotConfigEntity();
                cfg.setUserId(user.getId());
                cfg.setConfigKey(key);
                cfg.setConfigValue(value);
                cfg.setCreateDate(now);
                cfg.setUpdateDate(now);
                botConfigDao.insert(cfg);
            }
        }

        // 异步通知 Python 服务热重载（不能阻塞当前请求）
        try {
            webClient.post()
                    .uri(pythonBaseUrl + "/xiaozhi/config/reload")
                    .header("Service-Key",deviceServiceKey.isBlank()?"xiaozhi-device":deviceServiceKey)
                    .bodyValue(Map.of("wakeOnly", body.containsKey("customWakeWord") && body.keySet().stream().allMatch(k->k.equals("customWakeWord")||k.equals("customWakePinyin"))))
                    .retrieve()
                    .bodyToMono(String.class)
                    .subscribe(
                            r -> {},
                            e -> System.err.println("通知 Python 重载失败: " + e.getMessage())
                    );
        } catch (Exception e) {
            System.err.println("通知 Python 重载异常: " + e.getMessage());
        }

        return Result.ok();
    }

    @GetMapping("/wake-word-status")
    public Mono<Result<Object>> wakeWordStatus(@CurrentUser UserEntity user) {
        if (user == null) return Mono.just(Result.error(401, "未登录"));
        BotConfigEntity setting = botConfigDao.selectOne(new LambdaQueryWrapper<BotConfigEntity>()
                .eq(BotConfigEntity::getUserId, user.getId()).eq(BotConfigEntity::getConfigKey, "customWakeWord"));
        String word = setting == null || setting.getConfigValue() == null ? "" : setting.getConfigValue();
        return webClient.get().uri(pythonBaseUrl + "/xiaozhi/device/wake-word/status?word={word}", word)
                .retrieve().bodyToMono(Map.class)
                .map(result -> Result.<Object>ok(result.get("data")))
                .timeout(Duration.ofSeconds(6))
                .onErrorReturn(Result.ok(Map.of("state", "unreachable", "word", word)));
    }

    @GetMapping("/pub-config")
    public Result<Map<String, Object>> pubConfig() {
        Map<String, Object> config = new HashMap<>();
        config.put("version", "1.0.0");
        config.put("enableMobileRegister", true);
        return Result.ok(config);
    }
}
