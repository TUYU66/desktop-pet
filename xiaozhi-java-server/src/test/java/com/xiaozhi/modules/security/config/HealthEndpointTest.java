package com.xiaozhi.modules.security.config;

import com.xiaozhi.modules.device.controller.HealthController;
import com.xiaozhi.modules.user.dao.UserDao;
import com.xiaozhi.modules.security.dao.UserTokenDao;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Mono;
import reactor.test.StepVerifier;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class HealthEndpointTest {
    @Test void healthDoesNotDependOnAuthenticationOrUserDatabase() {
        var filter = new AuthFilter();
        var users = mock(UserDao.class);
        var tokens = mock(UserTokenDao.class);
        ReflectionTestUtils.setField(filter, "userDao", users);
        ReflectionTestUtils.setField(filter, "userTokenDao", tokens);
        var exchange = MockServerWebExchange.from(MockServerHttpRequest.get("/xiaozhi/api/health"));
        var called = new AtomicBoolean();
        StepVerifier.create(filter.filter(exchange, ignored -> {
            called.set(true);
            return Mono.empty();
        })).verifyComplete();
        assertTrue(called.get());
        verifyNoInteractions(users, tokens);
        assertEquals(Map.of("status", "online"), new HealthController().health());
    }

    @Test void healthSubpathsAreNotExemptFromAuthentication() {
        var exchange = MockServerWebExchange.from(MockServerHttpRequest.get("/xiaozhi/api/health/private"));
        var called = new AtomicBoolean();
        StepVerifier.create(new AuthFilter().filter(exchange, ignored -> {
            called.set(true);
            return Mono.empty();
        })).verifyComplete();
        assertFalse(called.get());
        assertEquals(HttpStatus.UNAUTHORIZED, exchange.getResponse().getStatusCode());
    }
}
