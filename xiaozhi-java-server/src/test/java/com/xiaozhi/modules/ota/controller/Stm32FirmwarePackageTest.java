package com.xiaozhi.modules.ota.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xiaozhi.modules.ota.service.Stm32FirmwarePackage;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Path;
import java.nio.file.Files;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.HashMap;
import java.util.Map;
import java.util.zip.CRC32;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import static org.junit.jupiter.api.Assertions.*;

class Stm32FirmwarePackageTest {
    @Test void upgradeGateRejectsInstalledAndOlderVersionsWithoutGuessingFailedTargets() {
        for(long candidate:new long[]{2026110901,2026110902})
            assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.requireNewerVersion(candidate,
                Map.of("installedVersion",2026110902)));
        assertDoesNotThrow(()->Stm32FirmwarePackage.requireNewerVersion(2026110903,Map.of("installedVersion",2026110902)));
        assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.requireNewerVersion(2026110902,
            Map.of("installedVersion",2026110901,"phase","completed","active",false,"version","2026110902")));
        assertDoesNotThrow(()->Stm32FirmwarePackage.requireNewerVersion(2026110902,
            Map.of("installedVersion",2026110901,"phase","failed","version","2026110902")));
        assertDoesNotThrow(()->Stm32FirmwarePackage.requireNewerVersion(2026110902,Map.of("installedVersion",0)));
        assertDoesNotThrow(()->Stm32FirmwarePackage.requireNewerVersion(2026110902,
            Map.of("installedVersion",2026110902,"recovery",true)));
    }
    @TempDir Path dir;
    byte[] image() {
        byte[] image=new byte[1024];
        ByteBuffer.wrap(image).order(ByteOrder.LITTLE_ENDIAN).putInt(0x2000C000).putInt(0x08008009);
        var descriptor=ByteBuffer.wrap(image).order(ByteOrder.LITTLE_ENDIAN);descriptor.position(64);
        descriptor.put("PETOTAAPPV1\0".getBytes(java.nio.charset.StandardCharsets.US_ASCII));
        descriptor.putInt(1).putInt(0x08008000).putInt(110592).putInt(8192).putInt(0x08008009);
        return image;
    }
    Map<String,Object> manifest(byte[] image) throws Exception {
        var m=new HashMap<String,Object>();
        m.put("format","desktop-pet-stm32-ota");m.put("format_version",1);m.put("layout_version",1);
        m.put("ota_application_abi",1);
        m.put("board","YB-EST01-V2.0");m.put("mcu","STM32F103RCT6");m.put("load_address",0x08008000);
        m.put("firmware_version",2026110404);m.put("original_size",image.length);m.put("image_size",image.length);
        var crc=new CRC32();crc.update(image);m.put("crc32",String.format("%08x",crc.getValue()));
        m.put("sha256",HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(image)));return m;
    }
    Path zip(Map<String,Object> manifest,byte[] image,String name) throws Exception {
        var file=Files.createTempFile(dir,"firmware",".zip");
        try(var zip=new ZipOutputStream(Files.newOutputStream(file))) {
            zip.putNextEntry(new ZipEntry("manifest.json"));zip.write(new ObjectMapper().writeValueAsBytes(manifest));zip.closeEntry();
            zip.putNextEntry(new ZipEntry(name));zip.write(image);zip.closeEntry();
        }return file;
    }
    @Test void acceptsPackagerFormatAndReturnsBoundedMetadata() throws Exception {
        var image=image();var result=Stm32FirmwarePackage.read(zip(manifest(image),image,"firmware.bin"));
        assertEquals(2026110404,result.version());assertArrayEquals(image,result.image());
        assertEquals(0x08008000,result.metadata().get("load_address"));
    }

    @Test void compiledReleaseVersionMustMatchManifest() throws Exception {
        var image=image();
        ByteBuffer.wrap(image).order(ByteOrder.LITTLE_ENDIAN).putInt(76,2).putInt(96,2026110404);
        assertEquals(2026110404,Stm32FirmwarePackage.read(zip(manifest(image),image,"firmware.bin")).version());
        ByteBuffer.wrap(image).order(ByteOrder.LITTLE_ENDIAN).putInt(96,2026110405);
        assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.read(zip(manifest(image),image,"firmware.bin")));
    }
    @Test void rejectsWrongModelCorruptionOldVectorsAndStringVersions() throws Exception {
        var image=image();
        for(var replacement:Map.of("mcu","wrong","firmware_version","2","sha256","bad","board","other").entrySet()) {
            var manifest=manifest(image);manifest.put(replacement.getKey(),replacement.getValue());
            assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.read(zip(manifest,image,"firmware.bin")));
        }
        var manifest=manifest(image);image[100]^=1;
        assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.read(zip(manifest,image,"firmware.bin")));
        ByteBuffer.wrap(image).order(ByteOrder.LITTLE_ENDIAN).putInt(4,0x08000101);
        assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.read(zip(manifest(image),image,"firmware.bin")));
    }
    @Test void rejectsZipPathsAndDecompressionBeyondSlot() throws Exception {
        var image=image();
        assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.read(zip(manifest(image),image,"../firmware.bin")));
        byte[] tooLarge=new byte[110594];
        assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.read(zip(manifest(image),tooLarge,"firmware.bin")));
    }
    @Test void refusesOffsetImagesWithoutARealOtaReleaseContract() throws Exception {
        var missing=image();java.util.Arrays.fill(missing,64,96,(byte)0);
        assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.read(zip(manifest(missing),missing,"firmware.bin")));
        var badEntry=image();ByteBuffer.wrap(badEntry).order(ByteOrder.LITTLE_ENDIAN).putInt(92,0x08000009);
        assertThrows(IllegalArgumentException.class,()->Stm32FirmwarePackage.read(zip(manifest(badEntry),badEntry,"firmware.bin")));
    }
    @Test void consumesDownloadGrantAndNeverServesAnEsp32Artifact() throws Exception {
        var service=org.mockito.Mockito.mock(com.xiaozhi.modules.ota.service.FirmwareService.class);
        var controller=new Stm32OtaController();
        org.springframework.test.util.ReflectionTestUtils.setField(controller,"firmwareService",service);
        var entity=new com.xiaozhi.modules.ota.entity.FirmwareEntity();
        byte[] image=image();entity.setBoardType(Stm32FirmwarePackage.BOARD_TYPE);
        entity.setFilePath(zip(manifest(image),image,"firmware.bin").toString());
        org.mockito.Mockito.when(service.getFirmwareIdByToken("test-token")).thenReturn(1L,null);
        org.mockito.Mockito.when(service.getById(1L)).thenReturn(entity);
        var first=controller.download("test-token").block();
        assertNotNull(first);assertEquals(200,first.getStatusCode().value());assertArrayEquals(image,first.getBody());
        assertEquals(404,controller.download("test-token").block().getStatusCode().value());
        org.mockito.Mockito.when(service.getFirmwareIdByToken("wrong-type")).thenReturn(1L);
        entity.setBoardType("bread-compact-wifi-lcd");
        assertEquals(404,controller.download("wrong-type").block().getStatusCode().value());
    }
    @Test void startUsesTheDeviceConfiguredOriginAndDoesNotReplayAnExistingJob() throws Exception {
        var service=org.mockito.Mockito.mock(com.xiaozhi.modules.ota.service.FirmwareService.class);
        var controller=new Stm32OtaController();
        var entity=new com.xiaozhi.modules.ota.entity.FirmwareEntity();
        byte[] image=image();entity.setBoardType(Stm32FirmwarePackage.BOARD_TYPE);
        entity.setFilePath(zip(manifest(image),image,"firmware.bin").toString());
        org.mockito.Mockito.when(service.getById(1L)).thenReturn(entity);
        org.mockito.Mockito.when(service.generateDownloadToken(1L)).thenReturn("a".repeat(32));
        var state=new java.util.concurrent.atomic.AtomicReference<String>(
            "{\"code\":0,\"data\":{\"chassisReady\":true,\"active\":false,\"jobId\":\"\",\"downloadOrigin\":\"http://192.168.0.2:8000\"}}");
        var methods=new java.util.ArrayList<org.springframework.http.HttpMethod>();
        var client=org.springframework.web.reactive.function.client.WebClient.builder().exchangeFunction(request->{
            methods.add(request.method());assertEquals("private-key",request.headers().getFirst("Service-Key"));
            String body=request.method()==org.springframework.http.HttpMethod.GET?state.get():"{\"code\":0,\"data\":{\"active\":true}}";
            return reactor.core.publisher.Mono.just(org.springframework.web.reactive.function.client.ClientResponse.create(org.springframework.http.HttpStatus.OK)
                .header("Content-Type","application/json").body(body).build());
        }).build();
        org.springframework.test.util.ReflectionTestUtils.setField(controller,"firmwareService",service);
        org.springframework.test.util.ReflectionTestUtils.setField(controller,"webClient",client);
        org.springframework.test.util.ReflectionTestUtils.setField(controller,"pythonBaseUrl","http://localhost:8004");
        org.springframework.test.util.ReflectionTestUtils.setField(controller,"serviceKey","private-key");
        org.springframework.test.util.ReflectionTestUtils.setField(controller,"publicBaseUrl","");
        assertEquals(0,controller.start(1L,Map.of("jobId","job1")).block().getCode());
        assertEquals(java.util.List.of(org.springframework.http.HttpMethod.GET,org.springframework.http.HttpMethod.POST),methods);
        methods.clear();org.mockito.Mockito.clearInvocations(service);
        state.set("{\"code\":0,\"data\":{\"active\":true,\"jobId\":\"job1\"}}");
        assertEquals(0,controller.start(1L,Map.of("jobId","job1")).block().getCode());
        assertEquals(java.util.List.of(org.springframework.http.HttpMethod.GET),methods);
        org.mockito.Mockito.verifyNoInteractions(service);
        methods.clear();
        state.set("{\"code\":0,\"data\":{\"chassisReady\":true,\"active\":false,\"recovery\":true,\"downloadOrigin\":\"http://192.168.0.2:8000\"}}");
        assertNotEquals(0,controller.start(1L,Map.of("jobId","rescue1")).block().getCode());
        assertEquals(java.util.List.of(org.springframework.http.HttpMethod.GET),methods);
        org.mockito.Mockito.verifyNoInteractions(service);
        methods.clear();
        assertEquals(0,controller.start(1L,Map.of("jobId","rescue1","confirmRecovery",true)).block().getCode());
        assertEquals(java.util.List.of(org.springframework.http.HttpMethod.GET,org.springframework.http.HttpMethod.POST),methods);

    }
}
