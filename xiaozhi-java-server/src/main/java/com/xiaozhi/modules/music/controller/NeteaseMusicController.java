package com.xiaozhi.modules.music.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.security.config.CurrentUser;
import com.xiaozhi.modules.user.dao.UserDao;
import com.xiaozhi.modules.user.entity.UserEntity;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpMethod;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.util.UriComponentsBuilder;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;
import java.time.Duration;
import java.util.Map;
import java.util.Set;

@RestController
@RequestMapping("/xiaozhi/api/music/netease")
public class NeteaseMusicController {
    @Resource private WebClient webClient;
    @Resource private UserDao userDao;
    @Value("${xiaozhi.python-server.base-url:http://localhost:8004}") private String pythonBaseUrl;
    @Value("${XIAOZHI_MUSIC_SERVICE_KEY:xiaozhi-music}") private String serviceKey;

    // This personal desktop-pet deployment has one account and one shared speaker.
    private Mono<Result<Object>> proxy(UserEntity user, HttpMethod method, String operation,
            Map<String,String> params, Map<String,Object> body) {
        return Mono.fromCallable(() -> {
            if(user == null) throw new IllegalArgumentException("请先登录项目账号");
            var owners = userDao.selectList(new LambdaQueryWrapper<UserEntity>().eq(UserEntity::getStatus, 1));
            if(owners.size()!=1 || !owners.get(0).getId().equals(user.getId()))
                throw new IllegalArgumentException("网易云音乐当前支持单用户个人部署");
            return true;
        }).subscribeOn(Schedulers.boundedElastic()).flatMap(ignored -> {
            var builder = UriComponentsBuilder.fromHttpUrl(pythonBaseUrl+"/xiaozhi/music/netease/"+operation);
            for(var entry:params.entrySet()) builder.queryParam(entry.getKey(), "{"+entry.getKey()+"}");
            var spec = webClient.method(method).uri(builder.encode().buildAndExpand(params).toUri())
                .header("Service-Key", serviceKey);
            WebClient.RequestHeadersSpec<?> request = body == null ? spec : spec.bodyValue(body);
            return request.exchangeToMono(response -> response.bodyToMono(Map.class).map(result -> {
                if(response.statusCode().is2xxSuccessful() && Integer.valueOf(0).equals(result.get("code")))
                    return Result.<Object>ok(result.get("data"));
                return Result.<Object>error(String.valueOf(result.getOrDefault("msg", "网易云请求未完成")));
            })).switchIfEmpty(Mono.just(Result.error("网易云服务没有返回结果")));
        }).timeout(Duration.ofSeconds(operation.equals("download") ? 240 : 65)).onErrorResume(error -> Mono.just(Result.error(
            error instanceof IllegalArgumentException ? error.getMessage() : "网易云服务连接失败或请求超时，请刷新核实结果")));
    }

    @GetMapping("/{operation}") public Mono<Result<Object>> read(@CurrentUser UserEntity user,
            @PathVariable String operation, @RequestParam Map<String,String> params) {
        if(!Set.of("account", "qr", "playlists", "songs", "search", "lyrics").contains(operation))
            return Mono.just(Result.error("网易云接口不存在"));
        var keys = switch(operation) {
            case "qr" -> Set.of("token");
            case "playlists" -> Set.of("offset");
            case "songs" -> Set.of("playlistId", "offset", "query");
            case "search" -> Set.of("artist", "title", "offset");
            case "lyrics" -> Set.of("trackId");
            default -> Set.<String>of();
        };
        if(!keys.containsAll(params.keySet())) return Mono.just(Result.error("网易云请求参数无效"));
        return proxy(user, HttpMethod.GET, operation, params, null);
    }
    @PostMapping("/qr") public Mono<Result<Object>> qr(@CurrentUser UserEntity user) {
        return proxy(user, HttpMethod.POST, "qr", Map.of(), null);
    }
    @PostMapping("/play") public Mono<Result<Object>> play(@CurrentUser UserEntity user, @RequestBody Map<String,Object> body) {
        return proxy(user, HttpMethod.POST, "play", Map.of(), body);
    }
    @DeleteMapping("/account") public Mono<Result<Object>> logout(@CurrentUser UserEntity user) {
        return proxy(user, HttpMethod.DELETE, "account", Map.of(), null);
    }
    @PostMapping("/download") public Mono<Result<Object>> download(@CurrentUser UserEntity user, @RequestBody Map<String,Object> body) {
        return proxy(user, HttpMethod.POST, "download", Map.of(), body);
    }
}
