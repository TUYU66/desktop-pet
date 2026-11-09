package com.xiaozhi.modules.ota.service;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.Arrays;

/** 按 ESP-IDF image header / esp_app_desc_t 读取应用，不接受合并镜像或 Bootloader。 */
public final class Esp32Firmware {
    public static final String BOARD = "bread-compact-wifi-lcd";
    public record Info(String version, String project) {}
    private Esp32Firmware() {}

    public static Info read(Path path) throws Exception {
        require(Files.size(path) >= 288 && Files.size(path) <= 0x3F0000, "固件大小超出 ESP32 应用分区");
        return read(Files.readAllBytes(path));
    }

    public static Info read(byte[] image) throws Exception {
        require(image.length >= 288 && image.length <= 0x3F0000, "不是有效的 ESP32 应用固件");
        var data = ByteBuffer.wrap(image).order(ByteOrder.LITTLE_ENDIAN);
        int segments = image[1] & 255;
        require((image[0] & 255) == 0xE9 && segments > 0 && segments <= 16 && data.getShort(12) == 9,
                "请选择 ESP32-S3 应用 BIN，不要上传整包或 Bootloader");
        require(data.getInt(28) >= 256 && data.getInt(32) == 0xABCD5432, "缺少 ESP32 应用描述信息");
        String version = text(image, 48), project = text(image, 80);
        require(version.matches("(0|[1-9][0-9]{0,8})\\.(0|[1-9][0-9]{0,8})\\.(0|[1-9][0-9]{0,8})"), "固件内部版本须为主版本.次版本.修订号");
        require(project.equals("xiaozhi"), "固件不属于本项目");
        int offset = 24, checksum = 0xEF;
        for (int i = 0; i < segments; i++) {
            require(offset + 8 <= image.length, "固件段头不完整");
            long length = Integer.toUnsignedLong(data.getInt(offset + 4));
            require(length <= image.length - offset - 8, "固件内容不完整");
            offset += 8;
            for (int end = offset + (int) length; offset < end; offset++) checksum ^= image[offset] & 255;
        }
        int checksumOffset = offset | 15;
        require(checksumOffset < image.length && (image[checksumOffset] & 255) == checksum, "固件校验失败");
        int end = checksumOffset + 1;
        require(image[23] == 1 && end + 32 == image.length, "固件缺少 SHA256 或包含额外镜像");
        require(MessageDigest.isEqual(MessageDigest.getInstance("SHA-256").digest(Arrays.copyOf(image, end)),
                Arrays.copyOfRange(image, end, image.length)), "固件 SHA256 校验失败");
        return new Info(version, project);
    }

    public static boolean newer(String candidate, String current) {
        if (candidate == null || current == null || !candidate.matches("[0-9]+\\.[0-9]+\\.[0-9]+") ||
                !current.matches("[0-9]+\\.[0-9]+\\.[0-9]+")) return false;
        String[] a = candidate.split("\\."), b = current.split("\\.");
        for (int i = 0; i < 3; i++) {
            int compare = new java.math.BigInteger(a[i]).compareTo(new java.math.BigInteger(b[i]));
            if (compare != 0) return compare > 0;
        }
        return false;
    }

    private static String text(byte[] image, int offset) {
        int end = offset;
        while (end < offset + 32 && image[end] != 0) end++;
        require(end < offset + 32, "固件描述字段没有结束符");
        return new String(image, offset, end - offset, StandardCharsets.US_ASCII);
    }
    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalArgumentException(message);
    }
}
