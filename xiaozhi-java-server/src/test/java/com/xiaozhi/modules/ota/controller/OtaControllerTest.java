package com.xiaozhi.modules.ota.controller;

import com.xiaozhi.modules.ota.service.FirmwareService;
import com.xiaozhi.modules.ota.entity.FirmwareEntity;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Path;
import com.xiaozhi.modules.ota.service.Stm32FirmwarePackage;

class OtaControllerTest {
    @TempDir Path directory;
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

    @Test void olderEsp32UploadIsNotOfferedOnStartup() {
        var service=mock(FirmwareService.class);
        var firmware=new FirmwareEntity();firmware.setVersion("2.2.9");
        when(service.getLatestVersion("board")).thenReturn(firmware);
        var result=controller(service).checkVersion("device","board","2.2.10","localhost:8000",null);
        assertFalse(result.containsKey("firmware"));
        verify(service,never()).generateDownloadToken(any());
    }

    @Test void startupOffersOnlyANewerVerifiedChassisRelease() throws Exception {
        var fixture=new Stm32FirmwarePackageTest();fixture.dir=directory;
        byte[] bytes=fixture.image();
        java.nio.ByteBuffer.wrap(bytes).order(java.nio.ByteOrder.LITTLE_ENDIAN).putInt(76,2).putInt(96,2026110404);
        Path zip=fixture.zip(fixture.manifest(bytes),bytes,"firmware.bin");
        var service=mock(FirmwareService.class);
        var firmware=new FirmwareEntity();firmware.setId(7L);firmware.setFilePath(zip.toString());
        when(service.getLatestVersion(Stm32FirmwarePackage.BOARD_TYPE)).thenReturn(firmware);
        when(service.generateDownloadToken(7L)).thenReturn("token");
        for(long installed:new long[]{2026110403L,2026110404L,2026110405L,0}) {
            var result=controller(service).checkVersion("device",null,null,"192.168.1.2:8000",
                Map.of("stm32",Map.of("installedVersion",installed,"chassisReady",true,"active",false)));
            assertEquals(installed==2026110403L,result.containsKey("stm32_firmware"));
        }
        verify(service,times(1)).generateDownloadToken(7L);
    }
}
