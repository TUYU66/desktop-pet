package com.xiaozhi.modules.ota.controller;

import com.xiaozhi.modules.ota.service.FirmwareService;
import com.xiaozhi.modules.ota.entity.FirmwareEntity;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class OtaControllerTest {
    private OtaController controller(FirmwareService service) {
        var controller = new OtaController();
        ReflectionTestUtils.setField(controller, "firmwareService", service);
        ReflectionTestUtils.setField(controller, "timezoneOffset", "8");
        ReflectionTestUtils.setField(controller, "wsPath", "/xiaozhi/v1/");
        ReflectionTestUtils.setField(controller, "serverPort", 8000);
        return controller;
    }

    @Test void readsEsp32JsonAndReturnsNumericMinutes() {
        var service = mock(FirmwareService.class);
        var result = controller(service).checkVersion("device", null, null, "192.168.0.101:8000",
                Map.of("board", Map.of("type", "bread-compact-wifi-lcd"),
                        "application", Map.of("version", "2.2.6")));
        verify(service).getLatestVersion("bread-compact-wifi-lcd");
        assertEquals(480, ((Map<?, ?>) result.get("server_time")).get("timezone_offset"));
        assertFalse(result.containsKey("firmware"));
    }

    @Test void unknownBoardNeverReceivesFirmware() {
        var service = mock(FirmwareService.class);
        var result = controller(service).checkVersion("device", null, null, "localhost:8000", null);
        assertFalse(result.containsKey("firmware"));
        verifyNoInteractions(service);
    }

    @Test void matchingVersionDoesNotCreateDownloadToken() {
        var service = mock(FirmwareService.class);
        var firmware = new FirmwareEntity();
        firmware.setVersion("2.2.6");
        when(service.getLatestVersion("board")).thenReturn(firmware);
        var result = controller(service).checkVersion("device", "board", "2.2.6", "localhost:8000", null);
        assertFalse(result.containsKey("firmware"));
        verify(service, never()).generateDownloadToken(any());
    }
}
