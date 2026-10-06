package com.xiaozhi.modules.memory.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.memory.dao.UserMemoryDao;
import com.xiaozhi.modules.memory.entity.UserMemoryEntity;
import com.xiaozhi.modules.memory.service.MemoryFactStore;
import com.xiaozhi.modules.memory.service.SemanticMemoryStore;
import com.xiaozhi.modules.security.config.CurrentUser;
import com.xiaozhi.modules.user.entity.UserEntity;
import jakarta.annotation.Resource;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;
import java.util.*;

/** 网页仅使用新语义协议；删除/清空不解析记录，方便重置测试数据。 */
@RestController
@RequestMapping("/xiaozhi/api/memories")
public class MemoryController {
    @Resource private UserMemoryDao memoryDao;
    @Resource private MemoryFactStore factStore;
    @Resource private SemanticMemoryStore semanticStore;

    private static String role(Object value) {
        return value==null||"".equals(value)?"default":SemanticMemoryStore.text(value,50);
    }
    private LambdaQueryWrapper<UserMemoryEntity> owned(Long owner,String role) {
        return new LambdaQueryWrapper<UserMemoryEntity>().eq(UserMemoryEntity::getUserId,owner).eq(UserMemoryEntity::getRoleId,role);
    }
    private UserMemoryEntity record(Long owner,Long id) {
        return memoryDao.selectOne(new LambdaQueryWrapper<UserMemoryEntity>().eq(UserMemoryEntity::getUserId,owner).eq(UserMemoryEntity::getId,id));
    }
    @GetMapping
    public Result<List<UserMemoryEntity>> list(@CurrentUser UserEntity user,@RequestParam(defaultValue="default") String roleId) {
        if(user==null) return Result.error(401,"未登录");
        try {
            return Result.ok(memoryDao.selectList(owned(user.getId(),role(roleId))
                .orderByDesc(UserMemoryEntity::getUpdateDate).orderByDesc(UserMemoryEntity::getId)).stream()
                .filter(m -> !"invalidated".equals(semanticStore.decode(m).get("status"))).toList());
        } catch(MemoryFactStore.Conflict e) { return Result.error(409,e.getMessage()); }
          catch(IllegalArgumentException e) { return Result.error(e.getMessage()); }
    }
    @PostMapping
    public Result<UserMemoryEntity> create(@CurrentUser UserEntity user,@RequestBody Map<String,Object> body) {
        if(user==null) return Result.error(401,"未登录");
        try {
            return Result.ok(semanticStore.manualCreate(user.getId(),role(body.get("roleId")),
                SemanticMemoryStore.text(body.get("category"),30),SemanticMemoryStore.text(body.get("key"),120),
                SemanticMemoryStore.text(body.get("content"),2000)));
        } catch(MemoryFactStore.Conflict e) { return Result.error(409,e.getMessage()); }
          catch(IllegalArgumentException e) { return Result.error(e.getMessage()); }
    }
    @PutMapping("/{id}")
    public Result<UserMemoryEntity> update(@CurrentUser UserEntity user,@PathVariable Long id,@RequestBody Map<String,Object> body) {
        if(user==null) return Result.error(401,"未登录");
        try {
            UserMemoryEntity current=record(user.getId(),id);
            if(current==null) return Result.error(404,"记忆不存在");
            if(!(body.get("version") instanceof Number)) return Result.error(409,"请携带记忆版本后编辑");
            if(SemanticMemoryStore.integer(body.get("version"))!=current.getVersion()) return Result.error(409,"记忆已变化，请刷新后编辑");
            return Result.ok(semanticStore.manualUpdate(user.getId(),current,SemanticMemoryStore.text(body.get("category"),30),
                SemanticMemoryStore.text(body.get("content"),2000),SemanticMemoryStore.text(body.get("key"),120)));
        } catch(MemoryFactStore.Conflict e) { return Result.error(409,e.getMessage()); }
          catch(IllegalArgumentException e) { return Result.error(e.getMessage()); }
    }
    @DeleteMapping("/{id}")
    @Transactional
    public Result<?> delete(@CurrentUser UserEntity user,@PathVariable Long id) {
        if(user==null) return Result.error(401,"未登录");
        UserMemoryEntity current=record(user.getId(),id);
        if(current==null) return Result.error(404,"记忆不存在");
        factStore.lock(user.getId(),current.getRoleId());
        semanticStore.purgeHistory(user.getId(),current.getRoleId(),id);
        memoryDao.deleteById(id);
        factStore.advance(user.getId(),current.getRoleId());
        return Result.ok();
    }
    @DeleteMapping
    @Transactional
    public Result<?> clear(@CurrentUser UserEntity user,@RequestParam(defaultValue="default") String roleId) {
        if(user==null) return Result.error(401,"未登录");
        String scope=role(roleId);
        factStore.lock(user.getId(),scope);
        semanticStore.purgeHistory(user.getId(),scope,null);
        memoryDao.delete(owned(user.getId(),scope));
        factStore.advance(user.getId(),scope);
        return Result.ok();
    }
}
