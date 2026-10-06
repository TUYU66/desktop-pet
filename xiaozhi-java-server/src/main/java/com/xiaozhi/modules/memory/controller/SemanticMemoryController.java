package com.xiaozhi.modules.memory.controller;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.memory.service.MemoryFactStore;
import com.xiaozhi.modules.memory.service.SemanticMemoryStore;
import com.xiaozhi.modules.security.config.CurrentUser;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.springframework.web.bind.annotation.*;
import java.util.*;
import static com.xiaozhi.modules.memory.service.SemanticMemoryStore.*;

@RestController
@RequestMapping("/xiaozhi/api/memories/semantic")
public class SemanticMemoryController {
    private final SemanticMemoryStore store;
    public SemanticMemoryController(SemanticMemoryStore store) { this.store=store; }
    @GetMapping("/revision")
    public Result<Map<String,Object>> revision(@CurrentUser UserEntity user,@RequestParam(defaultValue="default") String roleId) {
        if(user==null) return Result.error(401,"未登录");
        return Result.ok(Map.of("revision",store.revision(user.getId(),text(roleId,50))));
    }
    @PostMapping("/search")
    public Result<Map<String,Object>> search(@CurrentUser UserEntity user,@RequestBody Map<String,Object> body) {
        if(user==null) return Result.error(401,"未登录");
        try {
            return Result.ok(store.snapshot(user.getId(),text(body.getOrDefault("roleId","default"),50),integer(body.getOrDefault("afterId",0)),
                body.containsKey("expectedRevision")?integer(body.get("expectedRevision")):null));
        } catch(MemoryFactStore.Conflict e) { return Result.error(409,e.getMessage()); }
          catch(IllegalArgumentException e) { return Result.error(e.getMessage()); }
    }
    @PostMapping("/commit")
    public Result<Map<String,Integer>> commit(@CurrentUser UserEntity user,@RequestBody Map<String,Object> body) {
        if(user==null) return Result.error(401,"未登录");
        try {
            if(!Objects.equals(body.get("writeProtocolVersion"),PROTOCOL)) throw new IllegalArgumentException("请同步更新Python和Java记忆协议");
            if(!(body.get("operations") instanceof List<?> ops)) throw new IllegalArgumentException("缺少操作列表");
            return Result.ok(store.commit(user.getId(),text(body.getOrDefault("roleId","default"),50),integer(body.get("expectedRevision")),
                text(body.get("turnId"),64),text(body.getOrDefault("sessionId","unknown"),50),text(body.get("source"),2000),text(body.get("observedAt"),50),ops));
        } catch(MemoryFactStore.Conflict e) { return Result.error(409,e.getMessage()); }
          catch(IllegalArgumentException e) { return Result.error(e.getMessage()); }
    }
    @GetMapping("/{id}/history")
    public Result<List<Map<String,Object>>> history(@CurrentUser UserEntity user,@PathVariable long id,@RequestParam(defaultValue="default") String roleId) {
        if(user==null) return Result.error(401,"未登录");
        return Result.ok(store.history(user.getId(),text(roleId,50),id));
    }
}
