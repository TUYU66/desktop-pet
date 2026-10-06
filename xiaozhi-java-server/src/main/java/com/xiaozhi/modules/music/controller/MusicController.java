package com.xiaozhi.modules.music.controller;

import com.xiaozhi.common.result.Result;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.util.UriComponentsBuilder;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import java.time.Duration;
import java.util.Map;
import java.util.HashMap;
import java.util.ArrayList;
import java.util.List;
import com.xiaozhi.modules.device.dao.DeviceDao;
import reactor.core.scheduler.Schedulers;

@RestController
@RequestMapping("/xiaozhi/api/music")
public class MusicController {
    @Resource private WebClient webClient;
    @Resource private DeviceDao deviceDao;
    @Value("${xiaozhi.python-server.base-url:http://localhost:8004}") private String pythonBaseUrl;
    @Value("${XIAOZHI_MUSIC_SERVICE_KEY:xiaozhi-music}") private String serviceKey;

    private WebClient.RequestBodySpec request(HttpMethod method, String path) {
        return webClient.method(method).uri(pythonBaseUrl + path).header("Service-Key", serviceKey);
    }
    private Mono<Result<Object>> result(WebClient.RequestHeadersSpec<?> request) {
        return request.exchangeToMono(response -> response.bodyToMono(Map.class).map(body -> {
            if (response.statusCode().is2xxSuccessful() && Integer.valueOf(0).equals(body.get("code"))) {
                return Result.<Object>ok(body.get("data"));
            }
            return Result.<Object>error(String.valueOf(body.getOrDefault("msg", "音乐操作失败")));
        })).switchIfEmpty(Mono.just(Result.error("音乐服务未返回结果")))
          .timeout(Duration.ofSeconds(120)).onErrorReturn(Result.error("音乐服务连接失败或请求超时，请刷新确认结果"));
    }
    @GetMapping public Mono<Result<Object>> list() {
        return listWithDevices("/xiaozhi/music");
    }
    @GetMapping("/status") public Mono<Result<Object>> status() {
        return listWithDevices("/xiaozhi/music/status");
    }
    private Mono<Result<Object>> listWithDevices(String path) {
        return Mono.fromCallable(()-> {
            Map<String,String> names=new HashMap<>();
            for(var device:deviceDao.selectList(null)) {
                if(device.getMacAddress()!=null&&device.getDeviceModel()!=null&&!device.getDeviceModel().isBlank())
                    names.put(device.getMacAddress(),device.getDeviceModel());
            }
            return names;
        }).subscribeOn(Schedulers.boundedElastic()).onErrorReturn(Map.of()).flatMap(names->
            result(request(HttpMethod.GET,path)).map(response->{
                if(response.getData() instanceof Map<?,?> data && data.get("devices") instanceof List<?> devices) {
                    Map<Object,Object> copy=new HashMap<>(data);
                    List<Object> named=new ArrayList<>();
                    for(Object value:devices) {
                        if(value instanceof Map<?,?> device) {
                            Map<Object,Object> item=new HashMap<>(device);
                            String name=names.get(device.get("id"));
                            if(name!=null) item.put("name",name);
                            named.add(item);
                        } else named.add(value);
                    }
                    copy.put("devices",named); response.setData(copy);
                }
                return response;
            }));
    }
    @PostMapping("/control") public Mono<Result<Object>> control(@RequestBody Map<String,Object> body) {
        return result(request(HttpMethod.POST, "/xiaozhi/music/control").bodyValue(body));
    }
    @GetMapping("/details/{id}") public Mono<Result<Object>> details(@PathVariable String id) {
        if (!id.matches("[a-f0-9]{24}")) return Mono.just(Result.error("音乐编号无效"));
        return result(request(HttpMethod.GET, "/xiaozhi/music/details/"+id));
    }
    @DeleteMapping("/{id}") public Mono<Result<Object>> delete(@PathVariable String id) {
        if (!id.matches("[a-f0-9]{24}")) return Mono.just(Result.error("音乐编号无效"));
        return result(request(HttpMethod.DELETE, "/xiaozhi/music/" + id));
    }
    @PutMapping(consumes=MediaType.APPLICATION_OCTET_STREAM_VALUE)
    public Mono<Result<Object>> upload(@RequestParam String name, @RequestBody Flux<DataBuffer> content) {
        var uri = UriComponentsBuilder.fromHttpUrl(pythonBaseUrl + "/xiaozhi/music")
                .queryParam("name", "{name}").encode().buildAndExpand(name).toUri();
        return result(webClient.put().uri(uri).header("Service-Key", serviceKey)
                .contentType(MediaType.APPLICATION_OCTET_STREAM).body(content, DataBuffer.class));
    }
}
