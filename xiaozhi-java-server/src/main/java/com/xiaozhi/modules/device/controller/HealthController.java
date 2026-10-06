package com.xiaozhi.modules.device.controller;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Read-only process availability, independent of user/configuration queries. */
@RestController
public class HealthController {
    @GetMapping("/xiaozhi/api/health")
    public Map<String, String> health() {
        return Map.of("status", "online");
    }
}
