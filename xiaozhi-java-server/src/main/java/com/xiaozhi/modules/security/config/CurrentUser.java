package com.xiaozhi.modules.security.config;

import com.xiaozhi.modules.user.entity.UserEntity;
import org.springframework.core.MethodParameter;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.BindingContext;
import org.springframework.web.reactive.result.method.HandlerMethodArgumentResolver;
import org.springframework.web.reactive.result.method.annotation.ArgumentResolverConfigurer;
import org.springframework.web.reactive.config.WebFluxConfigurer;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.lang.annotation.*;

/**
 * 标注参数注入当前登录用户
 */
@Target(ElementType.PARAMETER)
@Retention(RetentionPolicy.RUNTIME)
@Documented
public @interface CurrentUser {
}

/**
 * 自动注入 @CurrentUser 注解的 UserEntity
 */
@Component
class CurrentUserArgumentResolver implements HandlerMethodArgumentResolver, WebFluxConfigurer {

    @Override
    public boolean supportsParameter(MethodParameter parameter) {
        return parameter.hasParameterAnnotation(CurrentUser.class)
                && parameter.getParameterType().equals(UserEntity.class);
    }

    @Override
    public Mono<Object> resolveArgument(MethodParameter parameter, BindingContext context, ServerWebExchange exchange) {
        UserEntity user = exchange.getAttribute("currentUser");
        return Mono.justOrEmpty(user);
    }

    @Override
    public void configureArgumentResolvers(ArgumentResolverConfigurer configurer) {
        configurer.addCustomResolver(this);
    }
}
