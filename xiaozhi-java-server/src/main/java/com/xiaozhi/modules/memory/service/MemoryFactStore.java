package com.xiaozhi.modules.memory.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xiaozhi.modules.memory.dao.UserMemoryDao;
import com.xiaozhi.modules.memory.entity.UserMemoryEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.time.LocalDateTime;
import java.util.*;

/** 数据库作用域锁 + 快照版本 + 幂等轮次；不能只依赖 Python 对象内的锁。 */
@Service
public class MemoryFactStore {
    private final UserMemoryDao dao;
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    public MemoryFactStore(UserMemoryDao dao, JdbcTemplate jdbc, ObjectMapper json) { this.dao=dao; this.jdbc=jdbc; this.json=json; }

    public long lock(Long owner, String role) {
        jdbc.update("INSERT IGNORE INTO user_memory_scope(user_id,role_id,revision) VALUES (?,?,0)", owner, role);
        return Objects.requireNonNull(jdbc.queryForObject("SELECT revision FROM user_memory_scope WHERE user_id=? AND role_id=? FOR UPDATE", Long.class, owner, role));
    }
    public void advance(Long owner, String role) {
        jdbc.update("UPDATE user_memory_scope SET revision=revision+1 WHERE user_id=? AND role_id=?", owner, role);
    }
    public static String normalized(String text) {
        return Normalizer.normalize(text, Normalizer.Form.NFKC).toLowerCase(Locale.ROOT).replaceAll("[^\\p{L}\\p{N}]", "");
    }
    public static String hash(String text) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    public static String identity(Map<?,?> fact) {
        String subject=(String)fact.get("subject"), predicate=(String)fact.get("predicate");
        String eid=fact.get("entityId") instanceof String s?s:"user".equals(subject)?"user":"e_"+hash(normalized(subject)).substring(0,40);
        String pid=fact.get("predicateId") instanceof String s?s:"p_"+hash(normalized(predicate)).substring(0,40);
        return hash(eid+"\u001f"+pid);
    }
    public static String slot(Map<?,?> fact) {
        String scope=(String)fact.get("temporalScope");
        return hash(identity(fact)+"\u001f"+scope+"\u001f"+("current".equals(scope)?"":fact.get("timePrecision")+"\u001f"+fact.get("timeValue")));
    }
    private LambdaQueryWrapper<UserMemoryEntity> owned(Long owner, String role) {
        return new LambdaQueryWrapper<UserMemoryEntity>().eq(UserMemoryEntity::getUserId,owner).eq(UserMemoryEntity::getRoleId,role);
    }

    @Transactional
    public Map<String,Object> search(Long owner, String role, List<String> keys, List<String> terms, boolean recall, boolean includeProfile) {
        long revision=lock(owner,role);
        LinkedHashMap<Long,UserMemoryEntity> found=new LinkedHashMap<>();
        if (recall) {
            dao.selectList(owned(owner,role).orderByDesc(UserMemoryEntity::getUpdateDate).orderByDesc(UserMemoryEntity::getId).last("LIMIT 101")).forEach(m->found.put(m.getId(),m));
        } else {
            // 当前事实槽优先，不被大量较新的历史记录挤出检索窗口。
            if(!keys.isEmpty()) dao.selectList(owned(owner,role).in(UserMemoryEntity::getSlotKey,keys.stream().map(k->hash(k+"\u001fcurrent\u001f")).toList()).last("LIMIT 12")).forEach(m->found.put(m.getId(),m));
            if(includeProfile) dao.selectList(owned(owner,role).eq(UserMemoryEntity::getCategory,"profile").orderByDesc(UserMemoryEntity::getUpdateDate).last("LIMIT 6")).forEach(m->found.put(m.getId(),m));
            if (!keys.isEmpty()) dao.selectList(owned(owner,role).in(UserMemoryEntity::getFactKey,keys).orderByDesc(UserMemoryEntity::getUpdateDate).last("LIMIT 61")).forEach(m->found.put(m.getId(),m));
            if (!terms.isEmpty()) dao.selectList(owned(owner,role).and(w->{ for(String term:terms) w.or().like(UserMemoryEntity::getContent,term).or().like(UserMemoryEntity::getFactJson,term); }).orderByDesc(UserMemoryEntity::getUpdateDate).last("LIMIT 61")).forEach(m->found.put(m.getId(),m));
        }
        List<UserMemoryEntity> records=new ArrayList<>(found.values());
        int cap=recall?100:60;
        boolean truncated=records.size()>cap;
        if(truncated) records=records.subList(0,cap);
        return Map.of("schemaVersion",2,"userId",owner,"roleId",role,"revision",revision,"memories",records,"truncated",truncated);
    }

    /** 提取读取有界属性目录；解析后精确查当前槽和选中ID，均保持用户/角色作用域。 */
    @Transactional
    public Map<String,Object> searchControlled(Long owner, String role, List<String> keys, List<String> terms, List<Long> ids, String stage, String queryText) {
        long revision=lock(owner,role);
        List<UserMemoryEntity> records;
        int cap;
        if("extract".equals(stage)) {
            cap=60;
            // 召回相关属性及别名；不因全库增长到60条就阻断所有自动记忆。
            var query=owned(owner,role).isNotNull(UserMemoryEntity::getFactJson);
            if(!terms.isEmpty()||!queryText.isEmpty()) query.and(w->{
                for(String term:terms) w.or().like(UserMemoryEntity::getContent,term).or().like(UserMemoryEntity::getFactJson,term);
                if(!queryText.isEmpty()) w.or().apply("LOCATE(NULLIF(REPLACE(REPLACE(REPLACE(JSON_UNQUOTE(JSON_EXTRACT(CASE WHEN JSON_VALID(fact_json) THEN fact_json ELSE '{}' END, '$.subject')), '用户的', ''), '用户', ''), 'user', ''), ''), {0}) > 0",queryText);
            });
            records=dao.selectList(query.orderByDesc(UserMemoryEntity::getUpdateDate).orderByDesc(UserMemoryEntity::getId).last("LIMIT 61"));
        } else if("resolve".equals(stage)) {
            cap=60;
            if(keys.isEmpty()&&ids.isEmpty()) records=List.of();
            else records=dao.selectList(owned(owner,role).and(w->{
                if(!keys.isEmpty()) w.in(UserMemoryEntity::getSlotKey,keys.stream().map(k->hash(k+"\u001fcurrent\u001f")).toList());
                if(!ids.isEmpty()) { if(!keys.isEmpty()) w.or(); w.in(UserMemoryEntity::getId,ids); }
            }).orderByDesc(UserMemoryEntity::getUpdateDate).orderByDesc(UserMemoryEntity::getId).last("LIMIT 61"));
        } else throw new IllegalArgumentException("检索阶段无效");
        boolean truncated=records.size()>cap;
        return Map.of("schemaVersion",2,"retrievalVersion",1,"writeProtocolVersion",3,"userId",owner,"roleId",role,"revision",revision,
            "memories",truncated?records.subList(0,cap):records,"truncated",truncated);
    }

    public static class Conflict extends RuntimeException { public Conflict(String message) { super(message); } }

    /** 历史只取自服务端旧记录；客户端不能提交或改写历史快照。 */
    public static Map<String,Object> transitionFact(Map<String,Object> old, String oldCategory,
            Map<String,Object> incoming, String category, String transition) {
        Map<String,Object> result=new LinkedHashMap<>(incoming);
        result.remove("stateHistory"); result.remove("goalStatus");
        if(old==null) {
            if(!"new".equals(transition)) throw new Conflict("新增事实的状态无效");
            return result;
        }
        boolean terminal=Set.of("completed","cancelled").contains(transition);
        if(terminal) {
            if(!"goal".equals(oldCategory)||!"current".equals(old.get("temporalScope"))||!"historical".equals(incoming.get("temporalScope"))
                ||!Objects.equals(old.get("entityId"),incoming.get("entityId"))
                ||!("completed".equals(transition)?"event":"goal").equals(category)) throw new Conflict("只能结束对应的进行中目标");
            if("cancelled".equals(transition)&&!identity(old).equals(identity(incoming))) throw new Conflict("取消目标身份不一致");
            if("completed".equals(transition)) {
                String originalCode=old.get("canonicalPredicate") instanceof String s?s:"attr_"+hash(normalized((String)old.get("predicate"))).substring(0,16);
                String completedCode="completed_"+originalCode;
                if(completedCode.length()>48) completedCode=completedCode.substring(0,48);
                if(!completedCode.equals(incoming.get("canonicalPredicate"))||!("p_"+hash(normalized(completedCode)).substring(0,40)).equals(incoming.get("predicateId")))
                    throw new Conflict("完成记录与原目标属性不一致");
            }
            result.put("goalStatus",transition);
        } else {
            if(!Set.of("changed","corrected","refines").contains(transition)||!slot(old).equals(slot(incoming))||!Objects.equals(oldCategory,category))
                throw new Conflict("事实状态或类别不一致");
        }
        List<Object> history=new ArrayList<>();
        if(old.get("stateHistory") instanceof List<?> previous) history.addAll(previous);
        if("changed".equals(transition)) {
            if(!"current".equals(old.get("temporalScope"))) throw new Conflict("历史事实更正不能伪装为当前状态变化");
            Map<String,Object> snapshot=new LinkedHashMap<>(old);
            snapshot.remove("stateHistory");
            snapshot.put("temporalScope","historical");
            Map<String,Object> entry=new LinkedHashMap<>();
            entry.put("category",oldCategory); entry.put("fact",snapshot);
            entry.put("endedAt",incoming.get("observedAt"));
            history.add(entry);
        }
        if(!history.isEmpty()) result.put("stateHistory",history);
        if(!terminal&&old.containsKey("goalStatus")) result.put("goalStatus",old.get("goalStatus"));
        return result;
    }

    @Transactional
    public Map<String,Integer> commit(Long owner, String role, long expectedRevision, String turn, List<Map<String,Object>> ops) {
        long actual=lock(owner,role);
        List<String> prior=jdbc.queryForList("SELECT result_json FROM user_memory_turn WHERE user_id=? AND role_id=? AND turn_id=?", String.class,owner,role,turn);
        if(!prior.isEmpty()) {
            try { return json.readValue(prior.get(0), json.getTypeFactory().constructMapType(Map.class,String.class,Integer.class)); }
            catch(Exception e) { throw new IllegalStateException(e); }
        }
        if(actual!=expectedRevision) throw new Conflict("记忆已变化，请重新读取后维护");
        int created=0,updated=0,deleted=0;
        Set<Long> touched=new HashSet<>();
        for(Map<String,Object> op:ops) {
            String action=(String)op.get("action");
            UserMemoryEntity old=null;
            if(!"add".equals(action)) {
                Long id=((Number)op.get("id")).longValue();
                old=dao.selectOne(owned(owner,role).eq(UserMemoryEntity::getId,id));
                if(old==null || !touched.add(id)) throw new Conflict("目标记忆不存在或重复操作");
                if(!Objects.equals(old.getVersion(), ((Number)op.get("expectedVersion")).intValue())) throw new Conflict("目标记忆版本变化");
                // 旧客户端不能通过v3删除/更新新语义记录；切换后必须使用v4入口。
                try {
                    if(old.getFactJson()!=null&&json.readTree(old.getFactJson()).path("schemaVersion").asInt()==3)
                        throw new Conflict("语义记忆需要新版写入协议");
                } catch(com.fasterxml.jackson.core.JsonProcessingException e) {
                    throw new Conflict("旧事实结构无法解析");
                }
            }
            if("delete".equals(action)) {
                if(!"invalidated".equals(op.get("transition"))) throw new Conflict("只有作废事实可删除");
                if(dao.deleteById(old.getId())!=1) throw new Conflict("删除未生效"); deleted++; continue;
            }
            @SuppressWarnings("unchecked") Map<String,Object> fact=(Map<String,Object>)op.get("fact");
            Map<String,Object> previous=null;
            if(old!=null) {
                try { previous=json.readValue(old.getFactJson(),json.getTypeFactory().constructMapType(Map.class,String.class,Object.class)); }
                catch(Exception e) { throw new Conflict("旧事实结构无法关联，请手动整理"); }
                if(previous==null) throw new Conflict("旧事实结构缺失");
            }
            fact=transitionFact(previous,old==null?null:old.getCategory(),fact,(String)op.get("category"),(String)op.get("transition"));
            String content=(String)op.get("content");
            if(fact.containsKey("goalStatus")) content=("completed".equals(fact.get("goalStatus"))?"已完成：":"已放弃：")+content;
            String slot=slot(fact);
            UserMemoryEntity duplicate=dao.selectOne(owned(owner,role).eq(UserMemoryEntity::getSlotKey,slot));
            if(duplicate!=null && (old==null || !duplicate.getId().equals(old.getId()))) {
                if(normalized(duplicate.getContent()).equals(normalized(content))) continue;
                throw new Conflict("同一事实状态已存在，应关联已有记录而不是新增");
            }
            Long count=old==null?dao.selectCount(owned(owner,role).eq(UserMemoryEntity::getContent,content)):0L;
            if(count!=null&&count>0) continue;
            UserMemoryEntity m=old==null?new UserMemoryEntity():old;
            if(old!=null && content.equals(old.getContent()) && Objects.equals(op.get("category"),old.getCategory())) continue;
            m.setUserId(owner); m.setRoleId(role); m.setContent(content); m.setCategory((String)op.get("category"));
            m.setFactKey(identity(fact)); m.setSlotKey(slot); m.setContentHash(hash(normalized(content)));
            try { m.setFactJson(json.writeValueAsString(fact)); } catch(Exception e) { throw new IllegalArgumentException("事实格式无效"); }
            m.setSourceType("conversation"); m.setSourceSessionId((String)op.get("sessionId")); m.setSourceTurnId(turn);
            m.setUpdateDate(LocalDateTime.now()); m.setVersion(old==null?0:old.getVersion()+1);
            if(old==null) { m.setCreateDate(m.getUpdateDate()); if(dao.insert(m)!=1) throw new Conflict("新增未生效"); created++; }
            else { if(dao.updateById(m)!=1) throw new Conflict("更新未生效"); updated++; }
        }
        Map<String,Integer> result=Map.of("created",created,"updated",updated,"deleted",deleted);
        try { jdbc.update("INSERT INTO user_memory_turn(user_id,role_id,turn_id,result_json,create_date) VALUES (?,?,?,?,?)",owner,role,turn,json.writeValueAsString(result),LocalDateTime.now()); }
        catch(Exception e) { throw new IllegalStateException(e); }
        if(created+updated+deleted>0) advance(owner,role);
        return result;
    }
}
