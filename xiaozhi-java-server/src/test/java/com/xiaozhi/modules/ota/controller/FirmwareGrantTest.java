package com.xiaozhi.modules.ota.controller;

import com.xiaozhi.modules.ota.service.FirmwareService;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import java.time.*;
import java.util.Objects;
import java.util.stream.IntStream;
import static org.junit.jupiter.api.Assertions.*;

class FirmwareGrantTest {
    @Test void grantIsConsumedOnlyOnceUnderConcurrency() {
        var service=new FirmwareService();
        var token=service.generateDownloadToken(7L);
        assertEquals(1,IntStream.range(0,32).parallel()
            .mapToObj(i->service.getFirmwareIdByToken(token)).filter(Objects::nonNull).count());
    }
    @Test void expiredGrantCannotDownloadFirmware() {
        var service=new FirmwareService();
        var time=Instant.parse("2026-10-18T00:00:00Z");
        ReflectionTestUtils.setField(service,"clock",Clock.fixed(time,ZoneOffset.UTC));
        var token=service.generateDownloadToken(7L);
        ReflectionTestUtils.setField(service,"clock",Clock.fixed(time.plusSeconds(300),ZoneOffset.UTC));
        assertNull(service.getFirmwareIdByToken(token));
    }
}
