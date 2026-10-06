package com.xiaozhi.modules.security.config;

import cn.hutool.core.util.StrUtil;
import com.xiaozhi.modules.security.dao.UserTokenDao;
import com.xiaozhi.modules.security.entity.UserTokenEntity;
import com.xiaozhi.modules.user.dao.UserDao;
import com.xiaozhi.modules.user.entity.UserEntity;
import jakarta.annotation.Resource;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;
import org.springframework.beans.factory.annotation.Value;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;

import java.time.LocalDateTime;

/**
 * WebFlux 认证过滤器，校验 Bearer Token，设置用户上下文
 */
@Component
public class AuthFilter implements WebFilter {

    @Resource
    private UserTokenDao userTokenDao;

    @Resource
    private UserDao userDao;
    @Value("${xiaozhi.memory-service-key:${XIAOZHI_MEMORY_SERVICE_KEY:}}")
    private String memoryServiceKey = "";
    @Value("${XIAOZHI_REMINDER_SERVICE_KEY:}") private String reminderServiceKey="";

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        String path = exchange.getRequest().getPath().value();
        String method = exchange.getRequest().getMethod().name();

        // 公开路径跳过认证
        if (isPublicPath(path)) {
            return chain.filter(exchange);
        }

        // 记忆服务不允许“多用户时随意选第一个”。浏览器仍使用正常 Bearer 鉴权。
        String serviceKey=exchange.getRequest().getHeaders().getFirst("Service-Key");
        if(path.startsWith("/xiaozhi/api/reminders/internal/")||path.startsWith("/xiaozhi/api/tasks/internal/")) {
            var remote=exchange.getRequest().getRemoteAddress();
            boolean local=remote!=null&&remote.getAddress()!=null&&remote.getAddress().isLoopbackAddress();
            String expected=reminderServiceKey.isBlank()?"xiaozhi-reminders":reminderServiceKey;
            if(serviceKey==null||reminderServiceKey.isBlank()&&!local||!MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),serviceKey.getBytes(StandardCharsets.UTF_8))) {
                exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED); return exchange.getResponse().setComplete();
            }
            return Mono.fromCallable(()->{
                var active=new LambdaQueryWrapper<UserEntity>().eq(UserEntity::getStatus,1);
                if(!Long.valueOf(1L).equals(userDao.selectCount(active))) return false;
                var user=userDao.selectOne(active);
                if(user==null) return false;
                exchange.getAttributes().put("currentUser",user); return true;
            }).subscribeOn(Schedulers.boundedElastic()).flatMap(ok->{
                if(ok) return chain.filter(exchange);
                exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED); return exchange.getResponse().setComplete();
            });
        }
        if((path.startsWith("/xiaozhi/api/reminders")||path.startsWith("/xiaozhi/api/tasks"))&&serviceKey!=null) {
            exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
            return exchange.getResponse().setComplete();
        }
        if(path.startsWith("/xiaozhi/api/memories") && serviceKey!=null) {
            return Mono.fromCallable(()-> {
                String expected=memoryServiceKey.isBlank()?"xiaozhi-python":memoryServiceKey;
                if(!MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),serviceKey.getBytes(StandardCharsets.UTF_8)))
                    return rejectMemory(exchange,"memory_service_key_invalid");
                String owner=exchange.getRequest().getHeaders().getFirst("Memory-Owner-Id");
                UserEntity user;
                if(owner!=null) {
                    // 显式选择用户必须配置私有服务密钥，不能用公开的兼容常量切换用户。
                    if(memoryServiceKey.isBlank()) return rejectMemory(exchange,"memory_owner_requires_private_key");
                    try { user=userDao.selectById(Long.valueOf(owner)); } catch(NumberFormatException e) { return rejectMemory(exchange,"memory_owner_invalid"); }
                } else {
                    var active=new LambdaQueryWrapper<UserEntity>().eq(UserEntity::getStatus,1);
                    if(!Long.valueOf(1L).equals(userDao.selectCount(active))) return rejectMemory(exchange,"memory_owner_ambiguous");
                    user=userDao.selectOne(active);
                }
                if(user==null || !Integer.valueOf(1).equals(user.getStatus())) return rejectMemory(exchange,"memory_owner_invalid");
                exchange.getAttributes().put("currentUser",user);
                return true;
            }).subscribeOn(Schedulers.boundedElastic()).flatMap(ok->{
                if(ok) return chain.filter(exchange);
                exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
                return exchange.getResponse().setComplete();
            });
        }

        // 其他个人版服务接口维持原约定；此处不再负责记忆接口。
        if ("xiaozhi-python".equals(exchange.getRequest().getHeaders().getFirst("Service-Key"))) {
            // 查第一个活跃用户作为上下文，让配置接口能正确返回数据
            return Mono.fromCallable(() -> {
                        UserEntity firstUser = userDao.selectOne(
                                new com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper<UserEntity>()
                                        .eq(UserEntity::getStatus, 1)
                                        .last("LIMIT 1")
                        );
                        if (firstUser != null) {
                            exchange.getAttributes().put("currentUser", firstUser);
                        }
                        return Boolean.TRUE;
                    })
                    .subscribeOn(Schedulers.boundedElastic())
                    .flatMap(success -> chain.filter(exchange));
        }

        // 只对 /xiaozhi/api/ 路径做认证
        if (!path.startsWith("/xiaozhi/api/")) {
            return chain.filter(exchange);
        }

        // 提取 token
        String token = extractToken(exchange);
        if (StrUtil.isBlank(token)) {
            exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
            return exchange.getResponse().setComplete();
        }

        // 在阻塞线程中执行 DB 查询，避免阻塞事件循环
        return Mono.fromCallable(() -> {
                    UserTokenEntity tokenEntity = userTokenDao.selectOne(
                            new com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper<UserTokenEntity>()
                                    .eq(UserTokenEntity::getToken, token)
                                    .gt(UserTokenEntity::getExpireDate, LocalDateTime.now())
                    );
                    if (tokenEntity == null) {
                        return Boolean.FALSE;
                    }
                    // 查找用户
                    UserEntity user = userDao.selectById(tokenEntity.getUserId());
                    if (user == null || user.getStatus() == 0) {
                        return Boolean.FALSE;
                    }
                    exchange.getAttributes().put("currentUser", user);
                    exchange.getAttributes().put("token", token);
                    return Boolean.TRUE;
                })
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(success -> {
                    if (!success) {
                        exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
                        return exchange.getResponse().setComplete();
                    }
                    return chain.filter(exchange);
                });
    }

    private boolean rejectMemory(ServerWebExchange exchange,String reason) {
        // 固定错误码方便服务间诊断；不暴露账户ID、数量或密钥。
        exchange.getResponse().getHeaders().set("X-Memory-Auth-Error",reason);
        return false;
    }

    private boolean isPublicPath(String path) {
        // 公开认证路径 + Python Server 上报路径
        return path.equals("/xiaozhi/api/health")
                || path.contains("/auth/login")
                || path.contains("/auth/register")
                || path.contains("/auth/captcha")
                || path.contains("/pub-config")
                || path.contains("/oauth")
                || path.contains("/chat/report")
                || path.contains("/chat/title/generate")
                || path.startsWith("/swagger-ui")
                || path.startsWith("/v3/api-docs")
                || path.startsWith("/doc.html")
                || path.startsWith("/webjars");
    }

    private String extractToken(ServerWebExchange exchange) {
        String auth = exchange.getRequest().getHeaders().getFirst("Authorization");
        if (StrUtil.isNotBlank(auth) && auth.startsWith("Bearer ")) {
            return auth.substring(7);
        }
        // 也支持 query 参数
        return exchange.getRequest().getQueryParams().getFirst("token");
    }
}
