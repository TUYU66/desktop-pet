package com.xiaozhi.modules.ota.controller;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.ota.entity.FirmwareEntity;
import com.xiaozhi.modules.ota.service.FirmwareService;
import com.xiaozhi.modules.ota.service.Stm32FirmwarePackage;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.codec.multipart.FilePart;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.Map;
import java.util.UUID;

@RestController
public class Stm32OtaController {
    @Resource private FirmwareService firmwareService;
    @Resource private WebClient webClient;
    @Value("${xiaozhi.python-server.base-url:http://localhost:8004}") private String pythonBaseUrl;
    @Value("${XIAOZHI_DEVICE_SERVICE_KEY:}") private String serviceKey;
    @Value("${xiaozhi.ota.public-base-url:}") private String publicBaseUrl;

    @PostMapping("/xiaozhi/api/admin/stm32-ota/upload")
    public Mono<Result<?>> upload(@RequestPart("file") FilePart file) {
        return Mono.fromCallable(()->{
            var dir=Path.of("data/firmware");Files.createDirectories(dir);
            return dir.resolve(UUID.randomUUID()+"-stm32.zip").toAbsolutePath();
        }).subscribeOn(Schedulers.boundedElastic()).flatMap(path->file.transferTo(path).then(Mono.<Result<?>>fromCallable(()->{
            var validated=Stm32FirmwarePackage.read(path);
            if(!validated.compiledVersion()) throw new IllegalArgumentException("请使用新版发布命令生成包含内部版本的 STM32 固件包");
            var latest=firmwareService.getLatestVersion(Stm32FirmwarePackage.BOARD_TYPE);
            if(latest!=null && Long.parseLong(latest.getVersion())>=validated.version())
                throw new IllegalArgumentException("该底盘版本已存在或低于已发布版本，请生成新版本后上传");
            var entity=new FirmwareEntity();entity.setBoardType(Stm32FirmwarePackage.BOARD_TYPE);
            entity.setVersion(Long.toString(validated.version()));entity.setFilePath(path.toString());
            entity.setFileSize(Files.size(path));entity.setDescription("STM32 底盘固件");entity.setStatus(1);
            entity.setForceUpdate(0);entity.setCreateDate(LocalDateTime.now());firmwareService.save(entity);
            return Result.ok(entity);
        }).subscribeOn(Schedulers.boundedElastic())).onErrorResume(error->Mono.<Result<?>>fromCallable(()->{
            Files.deleteIfExists(path);
            return Result.error(error instanceof IllegalArgumentException?error.getMessage():"固件包读取失败，请使用项目打包工具生成 ZIP");
        }).subscribeOn(Schedulers.boundedElastic())));
    }
    private FirmwareEntity firmware(long id) {
        var entity=firmwareService.getById(id);
        if(entity==null||!Stm32FirmwarePackage.BOARD_TYPE.equals(entity.getBoardType())) throw new IllegalArgumentException("STM32 固件不存在");
        return entity;
    }
    private Mono<Result<Object>> forward(String method,Map<String,Object> body) {
        return forwardTo("stm32-ota", method, body);
    }
    Mono<Result<Object>> forwardTo(String path,String method,Map<String,Object> body) {
        if(serviceKey==null||serviceKey.isBlank())return Mono.just(Result.error("设备服务认证尚未配置"));
        var request=webClient.method(org.springframework.http.HttpMethod.valueOf(method))
            .uri(pythonBaseUrl+"/xiaozhi/device/"+path).header("Service-Key",serviceKey);
        var spec=body==null?request:request.bodyValue(body);
        return spec.exchangeToMono(response->response.bodyToMono(Map.class).map(result->
            response.statusCode().is2xxSuccessful()&&Integer.valueOf(0).equals(result.get("code"))?
                Result.<Object>ok(result.get("data")):Result.<Object>error(String.valueOf(result.getOrDefault("msg","设备升级未确认")))))
            .switchIfEmpty(Mono.just(Result.error("设备未返回升级状态"))).timeout(Duration.ofSeconds(10))
            .onErrorReturn(Result.error("设备服务不可达，升级结果未知，请刷新状态，不要重复点击"));
    }
    @GetMapping("/xiaozhi/api/admin/stm32-ota/state")
    public Mono<Result<Object>> state() {return forward("GET",null);}

    @PostMapping("/xiaozhi/api/admin/stm32-ota/{id}/start")
    public Mono<Result<Object>> start(@PathVariable long id,@RequestBody Map<String,Object> input) {
        Object job=input.get("jobId");
        if(!(job instanceof String value)||!value.matches("[A-Za-z0-9_-]{1,64}"))return Mono.just(Result.error("升级任务编号无效"));
        return forward("GET",null).flatMap(state->{
            if(state.getCode()!=0)return Mono.just(state);
            if(!(state.getData() instanceof Map<?,?> actual))return Mono.just(Result.error("设备升级状态无效"));
            if(job.equals(actual.get("jobId")))return Mono.just(state);
            if(Boolean.TRUE.equals(actual.get("active")))return Mono.just(Result.error("已有升级正在进行，请刷新进度"));
            if(!Boolean.TRUE.equals(actual.get("chassisReady")))return Mono.just(Result.error("底盘尚未就绪，请确认首次烧录并刷新状态"));
            boolean recovery=Boolean.TRUE.equals(actual.get("recovery"));
            if(recovery&&!Boolean.TRUE.equals(input.get("confirmRecovery")))return Mono.just(Result.error("请先确认车体已固定、电机停止并使用稳定供电"));
            Object origin=actual.get("downloadOrigin");
            if(!(origin instanceof String))return Mono.just(Result.error("设备尚未报告固件服务地址"));
            return Mono.fromCallable(()->{
            var entity=firmware(id);
            var validated=Stm32FirmwarePackage.read(Path.of(entity.getFilePath()));
            Stm32FirmwarePackage.requireNewerVersion(validated.version(), actual);
            String base=(publicBaseUrl.isBlank()?(String)origin:publicBaseUrl).replaceAll("/+$","");
            var parsed=java.net.URI.create(base);
            if(!java.util.Set.of("http","https").contains(parsed.getScheme())||parsed.getHost()==null||parsed.getRawUserInfo()!=null||
               parsed.getRawQuery()!=null||parsed.getRawFragment()!=null||!parsed.getPath().isEmpty())throw new IllegalArgumentException("固件服务地址无效");
            String url=base+"/xiaozhi/ota/stm32/download/"+firmwareService.generateDownloadToken(id);
            var metadata=new java.util.HashMap<String,Object>(validated.metadata());
            metadata.put("recovery_confirmed",recovery);
            return Map.<String,Object>of("jobId",job,"url",url,"metadata",metadata);
        }).subscribeOn(Schedulers.boundedElastic()).flatMap(body->forward("POST",body))
          .onErrorResume(error->Mono.just(Result.error(error instanceof IllegalArgumentException?
                  error.getMessage():"固件已失效或升级服务配置错误，请刷新后重试")));
        });
    }
    @GetMapping("/xiaozhi/ota/stm32/download/{token}")
    public Mono<ResponseEntity<byte[]>> download(@PathVariable String token) {
        return Mono.fromCallable(()->{
            Long id=firmwareService.getFirmwareIdByToken(token);
            if(id==null)return ResponseEntity.status(404).<byte[]>build();
            var entity=firmware(id);
            byte[] image=Stm32FirmwarePackage.read(Path.of(entity.getFilePath())).image();
            firmwareService.incrementDownloadCount(id);
            return ResponseEntity.ok().contentType(MediaType.APPLICATION_OCTET_STREAM)
                .contentLength(image.length).header("Cache-Control","no-store").body(image);
        }).subscribeOn(Schedulers.boundedElastic()).onErrorReturn(ResponseEntity.status(404).build());
    }
}
