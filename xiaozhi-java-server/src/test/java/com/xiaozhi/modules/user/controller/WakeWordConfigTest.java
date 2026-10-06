package com.xiaozhi.modules.user.controller;

import com.xiaozhi.modules.user.dao.BotConfigDao;
import com.xiaozhi.modules.user.entity.BotConfigEntity;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.http.HttpStatus;
import reactor.core.publisher.Mono;
import java.util.List;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class WakeWordConfigTest {
    @Test void invalidPhraseNeverWrites() {
        var dao = mock(BotConfigDao.class);
        var controller = new UserController();
        ReflectionTestUtils.setField(controller, "botConfigDao", dao);
        var user = new UserEntity(); user.setId(1L);
        for (String word : List.of("hi robot", "小鹿", "你好 小智", "一二三四五六七八九")) {
            assertNotEquals(0, controller.saveConfig(user, Map.of("customWakeWord", word)).getCode());
        }
        verifyNoInteractions(dao);
    }

    @Test void savesChineseAndEmptyWithoutChangingOtherSettings() {
        var dao = mock(BotConfigDao.class);
        when(dao.selectList(any())).thenReturn(List.of());
        var controller = new UserController();
        ReflectionTestUtils.setField(controller, "botConfigDao", dao);
        ReflectionTestUtils.setField(controller, "pythonBaseUrl", "http://localhost");
        var web = WebClient.builder().exchangeFunction(request -> Mono.just(
                ClientResponse.create(HttpStatus.OK).body("{}").build())).build();
        ReflectionTestUtils.setField(controller, "webClient", web);
        var user = new UserEntity(); user.setId(1L);
        for (String word : List.of("你好朋友", "")) {
            assertEquals(0, controller.saveConfig(user, Map.of("customWakeWord", word)).getCode());
        }
        var values = org.mockito.ArgumentCaptor.forClass(BotConfigEntity.class);
        verify(dao, times(2)).insert(values.capture());
        assertEquals(List.of("你好朋友", ""), values.getAllValues().stream().map(BotConfigEntity::getConfigValue).toList());
        assertTrue(values.getAllValues().stream().allMatch(v -> v.getConfigKey().equals("customWakeWord")));
    }
}
