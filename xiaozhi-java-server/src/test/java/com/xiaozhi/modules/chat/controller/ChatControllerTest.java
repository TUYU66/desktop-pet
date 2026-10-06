package com.xiaozhi.modules.chat.controller;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;
import reactor.test.StepVerifier;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;

class ChatControllerTest {
    private ChatController controller(HttpStatus status, String body) {
        var controller = new ChatController();
        var client = WebClient.builder().exchangeFunction(request -> Mono.just(
                ClientResponse.create(status).header("Content-Type", "application/json")
                        .body(body).build())).build();
        ReflectionTestUtils.setField(controller, "webClient", client);
        ReflectionTestUtils.setField(controller, "pythonBaseUrl", "http://test.invalid");
        return controller;
    }

    @Test void forwardsWithoutBlockingReactiveThread() {
        var controller = controller(HttpStatus.OK, "{\"code\":0}");
        StepVerifier.create(Mono.defer(() -> controller.sendMessage(Map.of("text", "你好")))
                .subscribeOn(Schedulers.parallel()))
                .assertNext(result -> assertEquals(0, result.getCode())).verifyComplete();
    }

    @Test void offlineDeviceIsNotReportedAsSuccess() {
        var controller = controller(HttpStatus.NOT_FOUND, "{\"code\":404,\"msg\":\"没有在线设备\"}");
        StepVerifier.create(controller.sendMessage(Map.of("text", "你好")))
                .assertNext(result -> {
                    assertNotEquals(0, result.getCode());
                    assertEquals("没有在线设备", result.getMsg());
                }).verifyComplete();
    }

    @Test void emptyResponseIsNotReportedAsSuccess() {
        StepVerifier.create(controller(HttpStatus.OK, "").sendMessage(Map.of("text", "你好")))
                .assertNext(result -> assertNotEquals(0, result.getCode())).verifyComplete();
    }

    @Test void activeSessionsCanBeReadOnReactiveThread() {
        var controller = controller(HttpStatus.OK, "{\"code\":0,\"data\":[{\"sessionId\":\"s1\"}]}");
        StepVerifier.create(Mono.defer(controller::activeSessions).subscribeOn(Schedulers.parallel()))
                .assertNext(result -> assertEquals(java.util.List.of("s1"), result.getData())).verifyComplete();
    }
}
