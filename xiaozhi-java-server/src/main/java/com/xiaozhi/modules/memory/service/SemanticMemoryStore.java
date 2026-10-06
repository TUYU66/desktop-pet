package com.xiaozhi.modules.memory.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xiaozhi.modules.memory.dao.UserMemoryDao;
import com.xiaozhi.modules.memory.entity.UserMemoryEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.*;

/** 仅接受schemaVersion=3的语义记忆，不读取或迁移旧事实结构。 */
@Service
public class SemanticMemoryStore {
    public static final int PROTOCOL = 4;
    public static final Set<String> CATEGORIES = Set.of("profile","relationship","preference","habit","goal","event","note");
    private final UserMemoryDao dao;
    private final MemoryFactStore scopes;
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;

    public SemanticMemoryStore(UserMemoryDao dao, MemoryFactStore scopes, JdbcTemplate jdbc, ObjectMapper json) {
        this.dao=dao; this.scopes=scopes; this.jdbc=jdbc; this.json=json;
    }
    private LambdaQueryWrapper<UserMemoryEntity> owned(Long owner,String role) {
        return new LambdaQueryWrapper<UserMemoryEntity>().eq(UserMemoryEntity::getUserId,owner).eq(UserMemoryEntity::getRoleId,role);
    }
    public static String text(Object value,int cap) {
        if(!(value instanceof String s)||s.isBlank()||s.length()>cap) throw new IllegalArgumentException("语义记忆字段为空或过长");
        return s.trim();
    }
    public static long integer(Object value) {
        if(!(value instanceof Number n)||n.longValue()<0||n.doubleValue()!=n.longValue()) throw new IllegalArgumentException("ID或版本无效");
        return n.longValue();
    }
    public Map<String,Object> decode(UserMemoryEntity record) {
        try {
            Map<String,Object> m=json.readValue(record.getFactJson(),json.getTypeFactory().constructMapType(Map.class,String.class,Object.class));
            if(m!=null&&Objects.equals(m.get("schemaVersion"),3)
                && m.get("key") instanceof String && m.get("content") instanceof String
                && Set.of("active","closed","invalidated").contains(String.valueOf(m.get("status")))) return m;
        } catch(Exception ignored) { /* 在协议边界统一拒绝，禁止降级成旧正文。 */ }
        throw new MemoryFactStore.Conflict("记忆格式不符合当前协议，请清空旧数据后重新测试");
    }
    private String encode(Object value) {
        try { return json.writeValueAsString(value); } catch(Exception e) { throw new IllegalArgumentException("记忆格式无效",e); }
    }
    private Map<String,Object> preview(UserMemoryEntity r) {
        Map<String,Object> m=new LinkedHashMap<>(decode(r));
        m.put("id",r.getId()); m.put("version",r.getVersion()==null?0:r.getVersion());
        return m;
    }
    @Transactional
    public long revision(Long owner, String role) {
        return scopes.lock(owner, role);
    }
    @Transactional
    public Map<String,Object> snapshot(Long owner,String role,long after,Long expected) {
        long revision=scopes.lock(owner,role);
        if(expected!=null&&expected!=revision) throw new MemoryFactStore.Conflict("分页期间记忆已变化");
        List<UserMemoryEntity> rows=dao.selectList(owned(owner,role).gt(UserMemoryEntity::getId,after)
            .orderByAsc(UserMemoryEntity::getId).last("LIMIT 51"));
        boolean more=rows.size()>50;
        List<UserMemoryEntity> page=more?rows.subList(0,50):rows;
        return Map.of("schemaVersion",3,"writeProtocolVersion",PROTOCOL,"roleId",role,"revision",revision,
            "memories",page.stream().map(this::preview).toList(),"hasMore",more,
            "nextId",page.isEmpty()?after:page.get(page.size()-1).getId());
    }
    public static Map<String,Object> validateMemory(Map<?,?> raw,String source,String observed) {
        Map<String,Object> m=new LinkedHashMap<>();
        String category=text(raw.get("category"),30);
        if(!CATEGORIES.contains(category)) throw new IllegalArgumentException("记忆类别无效");
        m.put("schemaVersion",3); m.put("category",category);
        m.put("key",text(raw.get("key"),120)); m.put("content",text(raw.get("content"),2000));
        if(MemoryFactStore.normalized((String)m.get("key")).isEmpty()) throw new IllegalArgumentException("记忆主题无效");
        m.put("source",source); m.put("observedAt",observed);
        if(raw.containsKey("time")) m.put("time",text(raw.get("time"),120));
        return m;
    }
    public static String slot(Map<String,Object> m) {
        // 事件允许同主题多次发生；已关闭目标也不占当前状态槽。
        if("event".equals(m.get("category"))||!"active".equals(m.get("status"))) return null;
        return MemoryFactStore.hash("semantic\u001f"+m.get("category")+"\u001f"+MemoryFactStore.normalized((String)m.get("key")));
    }
    public static void validateTransition(Map<String,Object> old,Map<String,Object> next,String reason) {
        if(old==null) {
            if(!"new".equals(reason)) throw new IllegalArgumentException("新增原因无效");
            return;
        }
        if(!Set.of("changed","corrected","completed","cancelled","refines","invalidated").contains(reason))
            throw new IllegalArgumentException("更新原因无效");
        if("invalidated".equals(old.get("status"))) throw new MemoryFactStore.Conflict("无效记忆不能自动恢复");
        if("closed".equals(old.get("status"))&&!Set.of("corrected","invalidated").contains(reason))
            throw new MemoryFactStore.Conflict("已结束记忆只能纠错或作废，新一轮目标须新增");
        if(Set.of("completed","cancelled").contains(reason)) {
            if(!"goal".equals(old.get("category"))||!"active".equals(old.get("status"))) throw new MemoryFactStore.Conflict("目标不是进行中状态");
            if(!Objects.equals(next.get("category"),"goal")) throw new IllegalArgumentException("目标结束类别无效");
        }
        // 身份由所属用户/角色下的id与version控制；key是可变主题描述，不是身份。
    }
    @Transactional
    public Map<String,Integer> commit(Long owner,String role,long expected,String turn,String session,String source,String observed,List<?> operations) {
        text(source,2000);
        try { OffsetDateTime.parse(observed); } catch(Exception e) { throw new IllegalArgumentException("陈述时间必须含时区"); }
        if(operations.size()>8) throw new IllegalArgumentException("每轮最多8项");
        long revision=scopes.lock(owner,role);
        List<String> prior=jdbc.queryForList("SELECT result_json FROM user_memory_turn WHERE user_id=? AND role_id=? AND turn_id=?",String.class,owner,role,turn);
        if(!prior.isEmpty()) {
            try { return json.readValue(prior.get(0),json.getTypeFactory().constructMapType(Map.class,String.class,Integer.class)); }
            catch(Exception e) { throw new IllegalStateException(e); }
        }
        if(revision!=expected) throw new MemoryFactStore.Conflict("记忆已变化，请重新读取");
        int created=0,updated=0,deleted=0;
        Set<Long> touched=new HashSet<>();
        for(Object item:operations) {
            if(!(item instanceof Map<?,?> op)) throw new IllegalArgumentException("操作格式无效");
            String action=text(op.get("action"),12),reason=text(op.get("reason"),20);
            if(!Set.of("add","update","delete").contains(action)) throw new IllegalArgumentException("操作无效");
            UserMemoryEntity record=null; Map<String,Object> old=null;
            if(!"add".equals(action)) {
                long id=integer(op.get("targetId"));
                record=dao.selectOne(owned(owner,role).eq(UserMemoryEntity::getId,id));
                if(record==null||!touched.add(id)||integer(op.get("expectedVersion"))!=record.getVersion()) throw new MemoryFactStore.Conflict("目标不存在、重复或版本变化");
                old=decode(record);
            }
            Map<String,Object> next;
            if("delete".equals(action)) {
                if(!"invalidated".equals(reason)) throw new IllegalArgumentException("仅作废可删除");
                next=new LinkedHashMap<>(old); next.put("status","invalidated"); next.put("source",source); next.put("observedAt",observed);
            } else {
                if(!(op.get("memory") instanceof Map<?,?> raw)) throw new IllegalArgumentException("缺少记忆");
                next=validateMemory(raw,source,observed);
                boolean explicit=Boolean.TRUE.equals(op.get("explicit"));
                if("note".equals(next.get("category"))&&!explicit&&(old==null||!"explicit".equals(old.get("memoryMode")))) throw new IllegalArgumentException("备注需要主动授权");
                next.put("memoryMode",explicit||old!=null&&"explicit".equals(old.get("memoryMode"))?"explicit":"automatic");
                next.put("status",Set.of("completed","cancelled").contains(reason)?"closed":old==null?"active":old.get("status"));
            }
            validateTransition(old,next,reason);
            if("invalidated".equals(reason)&&!"delete".equals(action)) throw new IllegalArgumentException("作废需要delete操作");
            String slot=slot(next);
            if(slot!=null) {
                UserMemoryEntity duplicate=dao.selectOne(owned(owner,role).eq(UserMemoryEntity::getSlotKey,slot));
                if(duplicate!=null&&(record==null||!duplicate.getId().equals(record.getId()))) throw new MemoryFactStore.Conflict("同主题已有当前记忆");
            }
            if(old!=null&&Objects.equals(old.get("key"),next.get("key"))&&Objects.equals(old.get("content"),next.get("content"))&&Objects.equals(old.get("status"),next.get("status"))&&Objects.equals(old.get("category"),next.get("category"))&&Objects.equals(old.get("time"),next.get("time"))) continue;
            LocalDateTime now=LocalDateTime.now();
            if(record==null) { record=new UserMemoryEntity(); record.setCreateDate(now); record.setVersion(1); }
            else record.setVersion(record.getVersion()+1);
            record.setUserId(owner); record.setRoleId(role); record.setCategory((String)next.get("category")); record.setContent((String)next.get("content"));
            record.setFactJson(encode(next)); record.setFactKey(null); record.setSlotKey(slot);
            record.setContentHash(MemoryFactStore.hash(MemoryFactStore.normalized(record.getContent())));
            record.setSourceType("conversation"); record.setSourceSessionId(session); record.setSourceTurnId(turn); record.setUpdateDate(now);
            if(old==null) { if(dao.insert(record)!=1) throw new MemoryFactStore.Conflict("新增未生效"); created++; }
            else { if(dao.updateById(record)!=1) throw new MemoryFactStore.Conflict("更新未生效"); if("delete".equals(action)) deleted++; else updated++; }
            jdbc.update("INSERT INTO user_memory_history(user_id,role_id,memory_id,old_json,new_json,reason,source,turn_id,version,changed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                owner,role,record.getId(),old==null?null:encode(old),encode(next),reason,source,turn,record.getVersion(),now);
        }
        Map<String,Integer> result=Map.of("created",created,"updated",updated,"deleted",deleted);
        jdbc.update("INSERT INTO user_memory_turn(user_id,role_id,turn_id,result_json,create_date) VALUES (?,?,?,?,?)",owner,role,turn,encode(result),LocalDateTime.now());
        if(created+updated+deleted>0) scopes.advance(owner,role);
        return result;
    }
    public List<Map<String,Object>> history(Long owner,String role,long memoryId) {
        return jdbc.queryForList("SELECT id,old_json AS oldJson,new_json AS newJson,reason,source,version,changed_at AS changedAt FROM user_memory_history WHERE user_id=? AND role_id=? AND memory_id=? ORDER BY id DESC LIMIT 100",owner,role,memoryId);
    }
    @Transactional
    public UserMemoryEntity manualCreate(Long owner,String role,String category,String key,String content) {
        long revision=scopes.lock(owner,role);
        String turn="manual-"+UUID.randomUUID();
        Map<String,Object> memory=Map.of("category",category,"key",text(key,120),"content",content);
        commit(owner,role,revision,turn,"manual",content,OffsetDateTime.now().toString(),
            List.of(Map.of("action","add","reason","new","memory",memory,"explicit",true)));
        UserMemoryEntity record=dao.selectOne(owned(owner,role).eq(UserMemoryEntity::getSourceTurnId,turn));
        record.setSourceType("manual");
        dao.updateById(record);
        return record;
    }
    /** 网页修改是用户纠错；保持新结构及历史，不再降级成无结构正文。 */
    @Transactional
    public UserMemoryEntity manualUpdate(Long owner,UserMemoryEntity current,String category,String content,String key) {
        scopes.lock(owner,current.getRoleId());
        UserMemoryEntity record=dao.selectOne(owned(owner,current.getRoleId()).eq(UserMemoryEntity::getId,current.getId()));
        if(record==null||!Objects.equals(record.getVersion(),current.getVersion())) throw new MemoryFactStore.Conflict("记忆已变化");
        Map<String,Object> old=decode(record);
        if(!CATEGORIES.contains(category)) throw new IllegalArgumentException("类别无效");
        if(Objects.equals(old.get("category"),category)&&Objects.equals(old.get("content"),content)
            &&Objects.equals(old.get("key"),key)) return record;
        Map<String,Object> next=new LinkedHashMap<>(old);
        next.put("category",category); next.put("content",text(content,2000)); next.put("key",text(key,120));
        if(MemoryFactStore.normalized((String)next.get("key")).isEmpty()) throw new IllegalArgumentException("记忆主题无效");
        next.put("source",content); next.put("observedAt",OffsetDateTime.now().toString()); next.put("memoryMode","explicit");
        // 用户手动更正时间内容时，不沿用可能已失效的旧时间注解。
        next.remove("time");
        String slot=slot(next);
        if(slot!=null) {
            UserMemoryEntity duplicate=dao.selectOne(owned(owner,record.getRoleId()).eq(UserMemoryEntity::getSlotKey,slot));
            if(duplicate!=null&&!duplicate.getId().equals(record.getId())) throw new MemoryFactStore.Conflict("该主题已有当前记忆");
        }
        record.setCategory(category); record.setContent(content); record.setFactJson(encode(next)); record.setSlotKey(slot);
        record.setContentHash(MemoryFactStore.hash(MemoryFactStore.normalized(content)));
        record.setSourceType("manual"); record.setVersion(record.getVersion()+1); record.setUpdateDate(LocalDateTime.now());
        if(dao.updateById(record)!=1) throw new MemoryFactStore.Conflict("修改未生效");
        jdbc.update("INSERT INTO user_memory_history(user_id,role_id,memory_id,old_json,new_json,reason,source,turn_id,version,changed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            owner,record.getRoleId(),record.getId(),encode(old),encode(next),"corrected",content,"manual-"+UUID.randomUUID(),record.getVersion(),record.getUpdateDate());
        scopes.advance(owner,record.getRoleId());
        return record;
    }
    public void purgeHistory(Long owner,String role,Long id) {
        if(id==null) jdbc.update("DELETE FROM user_memory_history WHERE user_id=? AND role_id=?",owner,role);
        else jdbc.update("DELETE FROM user_memory_history WHERE user_id=? AND role_id=? AND memory_id=?",owner,role,id);
    }
}
