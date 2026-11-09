package com.xiaozhi.modules.ota.controller;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.ota.service.Esp32Firmware;
import com.xiaozhi.modules.ota.service.FirmwareService;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;
import java.nio.file.Path;
import java.util.Map;

@RestController
public class Esp32OtaController {
    @Resource private FirmwareService firmwareService;
    @Resource private Stm32OtaController bridge;
    @Value("${xiaozhi.ota.public-base-url:}") private String publicBaseUrl;

    @GetMapping("/xiaozhi/api/admin/esp32-ota/state")
    public Mono<Result<Object>> state() { return bridge.forwardTo("esp32-ota", "GET", null); }

    @PostMapping("/xiaozhi/api/admin/esp32-ota/{id}/start")
    public Mono<Result<Object>> start(@PathVariable long id, @RequestBody Map<String, Object> input) {
        if (!(input.get("jobId") instanceof String job) || !job.matches("[A-Za-z0-9_-]{1,64}") ||
                !(input.get("connectionId") instanceof String connection)) return Mono.just(Result.error("请刷新设备状态后重试"));
        return state().flatMap(result -> {
            if (result.getCode() != 0) return Mono.just(result);
            if (!(result.getData() instanceof Map<?,?> current)) return Mono.just(Result.error("设备状态无效"));
            if (job.equals(current.get("jobId"))) return Mono.just(result);
            if (!Boolean.TRUE.equals(current.get("ready")) || !connection.equals(current.get("connectionId")))
                return Mono.just(Result.error("设备已重连，请刷新后重新确认升级"));
            if (Boolean.TRUE.equals(current.get("active")) || Boolean.TRUE.equals(current.get("chassisActive")))
                return Mono.just(Result.error("已有升级正在进行，请等待完成"));
            return Mono.fromCallable(() -> {
                var entity = firmwareService.getById(id);
                if (entity == null || !Integer.valueOf(1).equals(entity.getStatus()) || !Esp32Firmware.BOARD.equals(entity.getBoardType()))
                    throw new IllegalArgumentException("ESP32 固件不存在或已禁用");
                var info = Esp32Firmware.read(Path.of(entity.getFilePath()));
                if (!info.version().equals(entity.getVersion()) || !entity.getBoardType().equals(current.get("boardType")))
                    throw new IllegalArgumentException("固件版本记录或设备型号不匹配，请重新上传固件");
                if (!Esp32Firmware.newer(info.version(), String.valueOf(current.get("currentVersion"))))
                    throw new IllegalArgumentException("请选择高于当前设备版本的固件");
                String base = publicBaseUrl.isBlank() ? String.valueOf(current.get("downloadOrigin")) : publicBaseUrl;
                base = base.replaceAll("/+$", "");
                var uri = java.net.URI.create(base);
                if (!java.util.Set.of("http", "https").contains(uri.getScheme()) || uri.getHost() == null ||
                        uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null || !uri.getPath().isEmpty())
                    throw new IllegalArgumentException("固件下载服务地址无效");
                return Map.<String,Object>of("jobId", job, "connectionId", connection, "version", info.version(),
                        "boardType", entity.getBoardType(), "url", base + "/xiaozhi/ota/download/" + firmwareService.generateDownloadToken(id));
            }).subscribeOn(Schedulers.boundedElastic())
                .flatMap(body -> bridge.forwardTo("esp32-ota", "POST", body))
                .onErrorResume(error -> Mono.just(Result.error(error instanceof IllegalArgumentException ? error.getMessage() : "固件读取失败")));
        });
    }
}
