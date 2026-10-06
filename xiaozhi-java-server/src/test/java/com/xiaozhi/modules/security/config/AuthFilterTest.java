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
import reactor.test.StepVerifier;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class AuthFilterTest {
    @Test void reminderInternalDefaultKeyRequiresLoopback() {
        checkReminderInternal("192.168.1.2","","xiaozhi-reminders",1,false);
        checkReminderInternal("127.0.0.1","","xiaozhi-reminders",1,true);
        checkReminderInternal("127.0.0.1","","xiaozhi-python",1,false);
    }
    @Test void reminderInternalRequiresSingleOwnerEvenWithPrivateKey() {
        checkReminderInternal("192.168.1.2","private-test","private-test",1,true);
        checkReminderInternal("192.168.1.2","private-test","private-test",2,false);
    }
    private void checkReminderInternal(String address,String configured,String supplied,long count,boolean valid) {
        var filter=new AuthFilter(); var users=mock(UserDao.class);
        ReflectionTestUtils.setField(filter,"userDao",users);
        ReflectionTestUtils.setField(filter,"reminderServiceKey",configured);
        var user=new UserEntity(); user.setId(7L); user.setStatus(1);
        when(users.selectCount(any())).thenReturn(count); when(users.selectOne(any())).thenReturn(user);
        var exchange=MockServerWebExchange.from(MockServerHttpRequest.get("/xiaozhi/api/reminders/internal/state")
            .remoteAddress(new java.net.InetSocketAddress(address,1234)).header("Service-Key",supplied));
        var called=new AtomicBoolean();
        StepVerifier.create(filter.filter(exchange,x->{called.set(true);return Mono.empty();})).verifyComplete();
        assertEquals(valid,called.get());
        if(!valid) assertEquals(HttpStatus.UNAUTHORIZED,exchange.getResponse().getStatusCode());
    }
    @Test void reminderBrowserApiRejectsCompatibilityServiceKey() {
        var exchange=MockServerWebExchange.from(MockServerHttpRequest.get("/xiaozhi/api/reminders").header("Service-Key","xiaozhi-python"));
        AtomicBoolean called=new AtomicBoolean();
        StepVerifier.create(new AuthFilter().filter(exchange,ignored->{called.set(true);return Mono.empty();})).verifyComplete();
        assertFalse(called.get()); assertEquals(HttpStatus.UNAUTHORIZED,exchange.getResponse().getStatusCode());
    }
    @Test void unknownTokenReturns401WithoutCallingController() {
        checkToken(false);
    }

    @Test void validTokenReachesController() {
        checkToken(true);
    }

    @Test void ambiguousServiceOwnerIsRejected() { checkService("",null,2L,false); }
    @Test void singleOwnerCompatibilityStillWorks() { checkService("",null,1L,true); }
    @Test void publicCompatibilityKeyCannotSelectAnotherOwner() { checkService("","7",1L,false); }
    @Test void privateKeyCanBindExplicitOwner() { checkService("private-test-key","7",2L,true); }

    private void checkService(String key,String owner,long count,boolean valid) {
        AuthFilter filter=new AuthFilter(); UserDao users=mock(UserDao.class);
        ReflectionTestUtils.setField(filter,"userDao",users);
        ReflectionTestUtils.setField(filter,"memoryServiceKey",key);
        UserEntity u=new UserEntity(); u.setId(7L); u.setStatus(1);
        when(users.selectCount(any())).thenReturn(count);
        when(users.selectOne(any())).thenReturn(u); when(users.selectById(7L)).thenReturn(u);
        var request=MockServerHttpRequest.post("/xiaozhi/api/memories/facts/search").header("Service-Key",key.isEmpty()?"xiaozhi-python":key);
        if(owner!=null) request.header("Memory-Owner-Id",owner);
        var exchange=MockServerWebExchange.from(request);
        AtomicBoolean called=new AtomicBoolean();
        StepVerifier.create(filter.filter(exchange,ignored->{called.set(true);return Mono.empty();})).verifyComplete();
        assertEquals(valid,called.get());
        if(!valid) {
            assertEquals(HttpStatus.UNAUTHORIZED,exchange.getResponse().getStatusCode());
            assertEquals(owner!=null&&key.isEmpty()?"memory_owner_requires_private_key":"memory_owner_ambiguous",
                exchange.getResponse().getHeaders().getFirst("X-Memory-Auth-Error"));
        }
        else assertEquals(7L,((UserEntity)exchange.getAttribute("currentUser")).getId());
    }

    private void checkToken(boolean valid) {
        AuthFilter filter = new AuthFilter();
        UserTokenDao tokens = mock(UserTokenDao.class);
        UserDao users = mock(UserDao.class);
        ReflectionTestUtils.setField(filter, "userTokenDao", tokens);
        ReflectionTestUtils.setField(filter, "userDao", users);
        if (valid) {
            UserTokenEntity token = new UserTokenEntity();
            token.setUserId(1L);
            UserEntity user = new UserEntity();
            user.setStatus(1);
            when(tokens.selectOne(any())).thenReturn(token);
            when(users.selectById(1L)).thenReturn(user);
        }
        var exchange = MockServerWebExchange.from(MockServerHttpRequest
                .get("/xiaozhi/api/user/info").header("Authorization", "Bearer test-token"));
        AtomicBoolean called = new AtomicBoolean();
        StepVerifier.create(filter.filter(exchange, ignored -> {
            called.set(true);
            return Mono.empty();
        })).verifyComplete();
        assertEquals(valid, called.get());
        if (!valid) assertEquals(HttpStatus.UNAUTHORIZED, exchange.getResponse().getStatusCode());
        else assertNotNull(exchange.getAttribute("currentUser"));
    }
}
