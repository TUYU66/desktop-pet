package com.xiaozhi.modules.ota.controller;

import com.xiaozhi.modules.ota.service.Esp32Firmware;
import org.junit.jupiter.api.Test;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.util.Arrays;
import static org.junit.jupiter.api.Assertions.*;

class Esp32FirmwareTest {
    byte[] image() throws Exception {
        byte[] raw = new byte[288];
        var b = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        raw[0]=(byte)0xE9;raw[1]=1;raw[23]=1;b.putShort(12,(short)9);
        b.putInt(28,256);b.putInt(32,0xABCD5432);
        System.arraycopy("2.2.10".getBytes(),0,raw,48,6);
        System.arraycopy("xiaozhi".getBytes(),0,raw,80,7);
        byte[] output=Arrays.copyOf(raw,336);
        int checksum=0xEF;
        for(int i=32;i<288;i++)checksum^=raw[i]&255;
        output[303]=(byte)checksum;
        byte[] hash=MessageDigest.getInstance("SHA-256").digest(Arrays.copyOf(output,304));
        System.arraycopy(hash,0,output,304,32);
        return output;
    }
    @Test void versionIsReadFromChecksummedImageNotUploadLabel() throws Exception {
        var info=Esp32Firmware.read(image());
        assertEquals("2.2.10",info.version());assertEquals("xiaozhi",info.project());
        assertTrue(Esp32Firmware.newer("2.2.10","2.2.9"));
        assertFalse(Esp32Firmware.newer("2.2.9","2.2.10"));
        assertFalse(Esp32Firmware.newer("2.2.10","2.2.10"));
    }
    @Test void rejectsWrongChipTruncationBootloaderAndCorruption() throws Exception {
        for(int offset:new int[]{0,12,32,48,80,100,303,335}) {
            var bytes=image();bytes[offset]^=1;
            assertThrows(IllegalArgumentException.class,()->Esp32Firmware.read(bytes));
        }
        var bytes=image();
        assertThrows(IllegalArgumentException.class,()->Esp32Firmware.read(Arrays.copyOf(bytes,bytes.length-1)));
        assertThrows(IllegalArgumentException.class,()->Esp32Firmware.read(Arrays.copyOf(bytes,bytes.length+1)));
        ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).putInt(28,Integer.MAX_VALUE);
        assertThrows(IllegalArgumentException.class,()->Esp32Firmware.read(bytes));
    }
}
