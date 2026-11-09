package com.xiaozhi.modules.ota.controller;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.ota.entity.FirmwareEntity;
import com.xiaozhi.modules.ota.service.Esp32Firmware;
import com.xiaozhi.modules.ota.service.FirmwareService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Mono;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class Esp32OtaControllerTest {
    @TempDir Path directory;
    final FirmwareService service=mock(FirmwareService.class);
    final Stm32OtaController bridge=mock(Stm32OtaController.class);

    Esp32OtaController controller() {
        var controller=new Esp32OtaController();
        ReflectionTestUtils.setField(controller,"firmwareService",service);
        ReflectionTestUtils.setField(controller,"bridge",bridge);
        ReflectionTestUtils.setField(controller,"publicBaseUrl","");
        return controller;
    }
    Map<String,Object> state(String version) {
        return Map.of("ready",true,"connectionId","session-a","active",false,"chassisActive",false,
                "boardType",Esp32Firmware.BOARD,"currentVersion",version,"downloadOrigin","http://192.168.1.2:8000");
    }
    void firmware() throws Exception {
        Path file=directory.resolve("application.bin");Files.write(file,new Esp32FirmwareTest().image());
        var entity=new FirmwareEntity();entity.setId(1L);entity.setStatus(1);entity.setVersion("2.2.10");
        entity.setBoardType(Esp32Firmware.BOARD);entity.setFilePath(file.toString());
        when(service.getById(1L)).thenReturn(entity);
        when(service.generateDownloadToken(1L)).thenReturn("a".repeat(32));
    }
    @Test void dispatchUsesValidatedBinaryAndConfirmedConnection() throws Exception {
        firmware();
        when(bridge.forwardTo("esp32-ota","GET",null)).thenReturn(Mono.just(Result.ok(state("2.2.9"))));
        when(bridge.forwardTo(eq("esp32-ota"),eq("POST"),anyMap())).thenAnswer(call -> {
            Map<?,?> body=call.getArgument(2);
            assertEquals("2.2.10",body.get("version"));assertEquals("session-a",body.get("connectionId"));
            assertEquals("http://192.168.1.2:8000/xiaozhi/ota/download/"+"a".repeat(32),body.get("url"));
            return Mono.just(Result.ok(Map.of("active",true,"phase","requested")));
        });
        var result=controller().start(1L,Map.of("jobId","job-1","connectionId","session-a")).block();
        assertNotNull(result);assertEquals(0,result.getCode());
        assertEquals("requested",((Map<?,?>)result.getData()).get("phase"));
        verify(bridge).forwardTo(eq("esp32-ota"),eq("POST"),anyMap());
    }
    @Test void reconnectAndSameVersionCannotStartUpdate() throws Exception {
        firmware();
        when(bridge.forwardTo("esp32-ota","GET",null)).thenReturn(Mono.just(Result.ok(state("2.2.10"))));
        assertNotEquals(0,controller().start(1L,Map.of("jobId","job","connectionId","session-old")).block().getCode());
        assertNotEquals(0,controller().start(1L,Map.of("jobId","job","connectionId","session-a")).block().getCode());
        verify(bridge,never()).forwardTo(eq("esp32-ota"),eq("POST"),anyMap());
        verify(service,never()).generateDownloadToken(any());
    }
}
