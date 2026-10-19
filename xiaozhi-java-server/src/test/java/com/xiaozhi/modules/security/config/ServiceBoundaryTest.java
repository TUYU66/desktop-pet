package com.xiaozhi.modules.security.config;

import com.xiaozhi.modules.security.dao.UserTokenDao;
import com.xiaozhi.modules.security.entity.UserTokenEntity;
import com.xiaozhi.modules.user.dao.UserDao;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Mono;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.any;

class ServiceBoundaryTest {
    @Test void corsPreflightReachesCorsProcessorButBareOptionsRequiresAuthentication() {
        var request = MockServerHttpRequest.options("/xiaozhi/api/user/config")
            .header("Origin", "http://localhost:5173")
            .header("Access-Control-Request-Method", "GET");
        var called = new AtomicBoolean();
        new AuthFilter().filter(MockServerWebExchange.from(request), ignored -> {
            called.set(true);
            return Mono.empty();
        }).block();
        assertTrue(called.get());
        var bare = MockServerWebExchange.from(MockServerHttpRequest.options("/xiaozhi/api/user/config"));
        new AuthFilter().filter(bare, ignored -> {
            fail("Bare OPTIONS must not skip authentication");
            return Mono.empty();
        }).block();
        assertEquals(HttpStatus.UNAUTHORIZED, bare.getResponse().getStatusCode());
    }
    @Test void publicStringCannotReadConfiguration() {
        check("/xiaozhi/api/user/config","xiaozhi-python",false);
    }
    @Test void privateServiceKeyCannotManageFirmware() {
        check("/xiaozhi/api/admin/firmware","private-test",false);
    }
    @Test void privateServiceKeyCanReadSingleOwnerConfiguration() {
        check("/xiaozhi/api/user/config","private-test",true);
    }
    @Test void privateServiceKeyCanResolveRoleSessionButCannotReadArbitraryChats() {
        check("/xiaozhi/api/chat/session-by-role/default", "private-test", true);
        check("/xiaozhi/api/chat/sessions", "private-test", false);
    }
    private void check(String path,String key,boolean allowed) {
        var filter=new AuthFilter(); var users=mock(UserDao.class);
        var user=new UserEntity(); user.setId(7L); user.setStatus(1);
        when(users.selectCount(any())).thenReturn(1L); when(users.selectOne(any())).thenReturn(user);
        ReflectionTestUtils.setField(filter,"userDao",users);
        ReflectionTestUtils.setField(filter,"pythonServiceKey","private-test");
        var exchange=MockServerWebExchange.from(MockServerHttpRequest.get(path).header("Service-Key",key));
        var called=new AtomicBoolean();
        filter.filter(exchange,ignored->{called.set(true);return Mono.empty();}).block();
        assertEquals(allowed,called.get());
        if(!allowed) assertEquals(HttpStatus.UNAUTHORIZED,exchange.getResponse().getStatusCode());
    }
    @Test void reportAndSubstringPathsAreNotAnonymous() {
        for(String path:new String[]{"/xiaozhi/api/chat/report","/xiaozhi/api/chat/title/generate","/xiaozhi/api/anything/oauth","/xiaozhi/api/anything/auth/login"}) {
            var exchange=MockServerWebExchange.from(MockServerHttpRequest.post(path));
            new AuthFilter().filter(exchange,ignored->{fail("Anonymous request reached handler");return Mono.empty();}).block();
            assertEquals(HttpStatus.UNAUTHORIZED,exchange.getResponse().getStatusCode());
        }
    }
    @Test void regularBearerUserCannotManageFirmware() {
        var filter=new AuthFilter(); var tokens=mock(UserTokenDao.class); var users=mock(UserDao.class);
        var token=new UserTokenEntity(); token.setUserId(7L);
        var user=new UserEntity(); user.setId(7L); user.setStatus(1); user.setSuperAdmin(0);
        when(tokens.selectOne(any())).thenReturn(token); when(users.selectById(7L)).thenReturn(user);
        ReflectionTestUtils.setField(filter,"userTokenDao",tokens); ReflectionTestUtils.setField(filter,"userDao",users);
        var exchange=MockServerWebExchange.from(MockServerHttpRequest.get("/xiaozhi/api/admin/firmware").header("Authorization","Bearer test"));
        filter.filter(exchange,ignored->{fail("Not an administrator");return Mono.empty();}).block();
        assertEquals(HttpStatus.FORBIDDEN,exchange.getResponse().getStatusCode());
    }
}
