package com.xiaozhi.modules.ota.controller;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.xiaozhi.common.exception.BusinessException;
import com.xiaozhi.common.exception.ErrorCode;
import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.ota.entity.FirmwareEntity;
import com.xiaozhi.modules.ota.service.FirmwareService;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.codec.multipart.FilePart;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Mono;

import java.io.File;
import java.nio.file.Paths;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.HashMap;
import java.util.Map;

@RestController
public class OtaController {

    @Value("${server.port:8000}")
    private int serverPort;

    @Value("${xiaozhi.ota.timezone-offset:+8}")
    private String timezoneOffset;

    @Value("${xiaozhi.websocket.path:/xiaozhi/v1/}")
    private String wsPath;

    @Resource
    private FirmwareService firmwareService;

    @PostMapping("/xiaozhi/ota/")
    public Map<String, Object> checkVersion(
            @RequestHeader(value = "Device-Id", required = false) String deviceId,
            @RequestHeader(value = "Device-Model", required = false) String deviceModel,
            @RequestHeader(value = "Device-Version", required = false) String deviceVersion,
            @RequestHeader(value = "Host", required = false) String host,
            @RequestBody(required = false) Map<String, Object> systemInfo) {

        Map<String, Object> result = new HashMap<>();

        ZonedDateTime now = ZonedDateTime.now(ZoneOffset.UTC);
        result.put("server_time", Map.of(
                "timestamp", now.toInstant().toEpochMilli(),
                "timezone_offset", timezoneOffsetMinutes()
        ));

        String wsHost = host != null ? host.split(":")[0] : "localhost";
        result.put("websocket", Map.of(
                "url", "ws://" + wsHost + ":8001" + wsPath
        ));

        // Current ESP32 firmware reports identity in JSON; retain old header clients.
        String boardType = nestedString(systemInfo, "board", "type", deviceModel);
        deviceVersion = nestedString(systemInfo, "application", "version", deviceVersion);
        FirmwareEntity latest = boardType == null || boardType.isBlank()
                ? null : firmwareService.getLatestVersion(boardType);
        if (latest != null && (deviceVersion == null || !latest.getVersion().equals(deviceVersion))) {
            String dlHost = host != null ? host.split(":")[0] : "localhost";
            result.put("firmware", Map.of(
                    "version", latest.getVersion(),
                    "url", "http://" + dlHost + ":" + serverPort + "/xiaozhi/ota/download/" +
                            firmwareService.generateDownloadToken(latest.getId())
            ));
        }

        return result;
    }

    private int timezoneOffsetMinutes() {
        // YAML may turn unquoted +8 into the string "8" after property conversion.
        String value = timezoneOffset.trim();
        ZoneOffset offset = value.matches("[+-]?\\d{1,2}")
                ? ZoneOffset.ofHours(Integer.parseInt(value)) : ZoneOffset.of(value);
        return offset.getTotalSeconds() / 60;
    }

    private String nestedString(Map<String, Object> body, String section, String key, String fallback) {
        if (body != null && body.get(section) instanceof Map<?, ?> fields
                && fields.get(key) instanceof String value && !value.isBlank()) {
            return value;
        }
        return fallback;
    }

    @GetMapping("/xiaozhi/ota/")
    public Map<String, Object> checkVersionGet(
            @RequestHeader(value = "Host", required = false) String host) {
        Map<String, Object> result = new HashMap<>();
        String wsHost = host != null ? host.split(":")[0] : "localhost";
        result.put("websocket", Map.of("url", "ws://" + wsHost + ":8001" + wsPath));
        return result;
    }

    @GetMapping("/xiaozhi/ota/download/{token}")
    public ResponseEntity<org.springframework.core.io.Resource> download(@PathVariable String token) {
        Long firmwareId = firmwareService.getFirmwareIdByToken(token);
        if (firmwareId == null) {
            throw new BusinessException(ErrorCode.FIRMWARE_NOT_FOUND);
        }

        FirmwareEntity firmware = firmwareService.getById(firmwareId);
        if (firmware == null) {
            throw new BusinessException(ErrorCode.FIRMWARE_NOT_FOUND);
        }

        File file = new File(firmware.getFilePath());
        if (!file.exists()) {
            throw new BusinessException(ErrorCode.FIRMWARE_NOT_FOUND, "固件文件不存在");
        }

        firmwareService.incrementDownloadCount(firmwareId);

        org.springframework.core.io.Resource resource = new FileSystemResource(file);
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        "attachment; filename=" + firmware.getVersion() + ".bin")
                .body(resource);
    }

    @GetMapping("/xiaozhi/api/admin/firmware")
    public Result<Page<FirmwareEntity>> firmwareList(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "10") int size) {
        return Result.ok(firmwareService.page(page, size));
    }

    @PostMapping("/xiaozhi/api/admin/firmware")
    public Mono<Result<?>> uploadFirmware(
            @RequestPart("version") String version,
            @RequestPart(value = "description", required = false) String description,
            @RequestPart("boardType") String boardType,
            @RequestPart(value = "forceUpdate", required = false) String forceUpdate,
            @RequestPart("file") FilePart filePart) {

        String filename = filePart.filename();
        // 防止路径穿越：只取文件名，丢弃路径部分
        String safeName = Paths.get(filename).getFileName().toString();
        if (safeName == null || safeName.isBlank()) {
            return Mono.just(Result.error("无效的文件名"));
        }
        // 确保文件名只含安全字符（字母、数字、.、-、_）
        if (!safeName.matches("[\\w.-]+")) {
            return Mono.just(Result.error("文件名包含非法字符"));
        }
        File targetDir = new File("data/firmware");
        if (!targetDir.exists()) targetDir.mkdirs();
        File target = new File(targetDir, safeName);
        return filePart.transferTo(target)
                .then(Mono.fromCallable(() -> {
                    FirmwareEntity entity = new FirmwareEntity();
                    entity.setVersion(version);
                    entity.setDescription(description);
                    entity.setBoardType(boardType);
                    entity.setForceUpdate(forceUpdate != null ? Integer.parseInt(forceUpdate) : 0);
                    entity.setFilePath(target.getAbsolutePath());
                    entity.setFileSize(target.length());
                    entity.setStatus(1);
                    entity.setCreateDate(LocalDateTime.now());
                    firmwareService.save(entity);
                    return Result.ok();
                }));
    }

    @DeleteMapping("/xiaozhi/api/admin/firmware/{id}")
    public Result<?> deleteFirmware(@PathVariable Long id) {
        firmwareService.deleteFirmware(id);
        return Result.ok();
    }
}
