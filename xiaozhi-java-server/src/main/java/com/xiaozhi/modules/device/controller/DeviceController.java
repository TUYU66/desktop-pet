package com.xiaozhi.modules.device.controller;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.device.entity.DeviceEntity;
import com.xiaozhi.modules.device.service.DeviceService;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpMethod;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import java.time.Duration;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/xiaozhi/api/devices")
public class DeviceController {

    @Resource
    private DeviceService deviceService;

    @Resource private WebClient webClient;
    @Value("${xiaozhi.python-server.base-url:http://localhost:8004}")
    private String pythonBaseUrl;

    @Value("${XIAOZHI_DEVICE_SERVICE_KEY:}") private String panelKey;

    @GetMapping("/panel")
    public Mono<Result<Object>> panel(@RequestParam(required=false) String deviceId) {
        return forwardPanel(HttpMethod.GET, deviceId, null);
    }
    @PostMapping("/panel")
    public Mono<Result<Object>> controlPanel(@RequestBody Map<String,Object> body) {
        return forwardPanel(HttpMethod.POST, null, body);
    }
    @GetMapping("/tuning")
    public Mono<Result<Object>> tuning(@RequestParam String deviceId,
                                      @RequestParam(defaultValue="0") int after,
                                      @RequestParam(defaultValue="0") int epoch) {
        return forwardPanel(HttpMethod.GET, deviceId, null, "/tuning?after="+after+"&epoch="+epoch);
    }
    @PostMapping("/tuning")
    public Mono<Result<Object>> tune(@RequestBody Map<String,Object> body) {
        return forwardPanel(HttpMethod.POST, null, body, "/tuning");
    }
    @PostMapping("/calibration")
    public Mono<Result<Object>> calibration(@RequestBody Map<String,Object> body) {
        return forwardPanel(HttpMethod.POST, null, body, "/calibration");
    }
    private Mono<Result<Object>> forwardPanel(HttpMethod method,String device,Map<String,Object> body) {
        return forwardPanel(method, device, body, "/panel");
    }
    private Mono<Result<Object>> forwardPanel(HttpMethod method,String device,Map<String,Object> body,String path) {
        var uri=org.springframework.web.util.UriComponentsBuilder.fromUriString(pythonBaseUrl+"/xiaozhi/device"+path);
        if(device!=null) uri.queryParam("deviceId",device);
        var request=webClient.method(method).uri(uri.build().encode().toUri())
            .header("Service-Key",panelKey.isBlank()?"xiaozhi-device":panelKey);
        var spec=body==null?request:request.bodyValue(body);
        return spec.exchangeToMono(response->response.bodyToMono(Map.class).map(result->{
            if(response.statusCode().is2xxSuccessful()&&Integer.valueOf(0).equals(result.get("code"))) return Result.<Object>ok(result.get("data"));
            return Result.<Object>error(String.valueOf(result.getOrDefault("msg","设备操作未确认")));
        })).switchIfEmpty(Mono.just(Result.error("设备没有返回状态")))
          .timeout(Duration.ofSeconds(16)).onErrorReturn(Result.error("设备服务不可达或请求超时，执行结果未知，请刷新核实"));
    }

    @GetMapping("/volume")
    public Mono<Result<Object>> volume() {
        return forwardVolume(HttpMethod.GET, null);
    }

    @PutMapping("/volume")
    public Mono<Result<Object>> setVolume(@RequestBody Map<String, Object> body) {
        Object value = body.get("volume");
        if (!(value instanceof Integer) || (Integer) value < 0 || (Integer) value > 100) {
            return Mono.just(Result.error("音量必须为 0～100 的整数"));
        }
        return forwardVolume(HttpMethod.PUT, body);
    }

    private Mono<Result<Object>> forwardVolume(HttpMethod method, Map<String, Object> body) {
        var request = webClient.method(method).uri(pythonBaseUrl + "/xiaozhi/device/volume");
        var spec = body == null ? request : request.bodyValue(body);
        return spec.exchangeToMono(response -> response.bodyToMono(Map.class).map(result -> {
                    if (response.statusCode().is2xxSuccessful() && Integer.valueOf(0).equals(result.get("code"))) {
                        return Result.<Object>ok(result.get("data"));
                    }
                    return Result.<Object>error(String.valueOf(result.getOrDefault("msg", "设备未确认音量")));
                }))
                .switchIfEmpty(Mono.just(Result.error("设备未返回音量")))
                .timeout(Duration.ofSeconds(18))
                .onErrorReturn(Result.error("设备服务不可达或音量请求超时"));
    }

    @GetMapping("/info")
    public Result<DeviceEntity> info() {
        return Result.ok(deviceService.getDeviceInfo());
    }

    @PostMapping("/heartbeat")
    public Result<?> heartbeat(@RequestBody Map<String, Object> body) {
        Object deviceIdObj = body.get("deviceId");
        if (deviceIdObj == null) {
            return Result.error("deviceId 不能为空");
        }
        Long deviceId = Long.valueOf(deviceIdObj.toString());
        String status = (String) body.getOrDefault("status", "online");
        Object batteryObj = body.get("battery");
        deviceService.updateStatus(deviceId, status);
        if (batteryObj != null) {
            deviceService.updateBattery(deviceId, Integer.valueOf(batteryObj.toString()));
        }
        return Result.ok();
    }
}
