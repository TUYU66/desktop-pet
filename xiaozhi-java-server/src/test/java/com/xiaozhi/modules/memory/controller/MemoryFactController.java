package com.xiaozhi.modules.memory.controller;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.memory.service.MemoryFactStore;
import com.xiaozhi.modules.security.config.CurrentUser;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.springframework.web.bind.annotation.*;
import java.time.LocalDate;
import java.util.*;

/** v2 自动维护入口：持久化事实结构，旧网页正文接口仍可读取和手动整理。 */
// v3控制器已退出路由注册；仅保留静态辅助方法供历史单元测试引用。
public class MemoryFactController {
    private final MemoryFactStore store;
    public MemoryFactController(MemoryFactStore store) { this.store=store; }
    @PostMapping("/search")
    public Result<Map<String,Object>> search(@CurrentUser UserEntity user,@RequestBody Map<String,Object> body) {
        if(user==null) return Result.error(401,"未登录");
        try {
            if(body.containsKey("controlledStage")) {
                String stage=text(body.get("controlledStage"),12);
                if(!Set.of("extract","resolve").contains(stage)) throw new IllegalArgumentException("检索阶段无效");
                List<Long> ids=new ArrayList<>();
                Object rawIds=body.getOrDefault("ids",List.of());
                if(!(rawIds instanceof List<?> list)||list.size()>8) throw new IllegalArgumentException("目标最多8项");
                for(Object id:list) ids.add(integer(id));
                String queryText=body.containsKey("controlledQuery")&&!"".equals(body.get("controlledQuery"))?text(body.get("controlledQuery"),2400):"";
                return Result.ok(store.searchControlled(user.getId(),text(body.getOrDefault("roleId","default"),50),
                    strings(body.get("keys"),32,64),strings(body.get("terms"),12,60),ids,stage,queryText));
            }
            return Result.ok(store.search(user.getId(),text(body.getOrDefault("roleId","default"),50),
                strings(body.get("keys"),12,64),strings(body.get("terms"),12,60),Boolean.TRUE.equals(body.get("recall")),Boolean.TRUE.equals(body.get("includeProfile"))));
        } catch(IllegalArgumentException e) { return Result.error(e.getMessage()); }
    }
    @PostMapping("/commit")
    public Result<Map<String,Integer>> commit(@CurrentUser UserEntity user,@RequestBody Map<String,Object> body) {
        if(user==null) return Result.error(401,"未登录");
        try {
            if(!Objects.equals(body.get("writeProtocolVersion"),3)) throw new IllegalArgumentException("请同步更新记忆服务，写入协议需要版本3");
            String role=text(body.getOrDefault("roleId","default"),50), turn=text(body.get("turnId"),64);
            long revision=integer(body.get("expectedRevision"));
            if(!(body.get("operations") instanceof List<?> raw) || raw.size()>8) throw new IllegalArgumentException("事实操作最多8项");
            List<Map<String,Object>> ops=new ArrayList<>();
            for(Object item:raw) {
                if(!(item instanceof Map<?,?> values)) throw new IllegalArgumentException("操作格式无效");
                String action=text(values.get("action"),10);
                if(!Set.of("add","update","delete").contains(action)) throw new IllegalArgumentException("操作类型无效");
                Map<String,Object> op=new HashMap<>(); op.put("action",action);
                String transition=text(values.get("transition"),16);
                if(!(switch(action) { case "add" -> "new".equals(transition); case "delete" -> "invalidated".equals(transition); default -> Set.of("refines","changed","corrected","completed","cancelled").contains(transition); }))
                    throw new IllegalArgumentException("状态变更类型无效");
                op.put("transition",transition);
                if(!"add".equals(action)) { op.put("id",integer(values.get("id"))); op.put("expectedVersion",integer(values.get("expectedVersion"))); }
                if(!"delete".equals(action)) {
                    if(!(values.get("fact") instanceof Map<?,?> f)) throw new IllegalArgumentException("缺少事实结构");
                    Map<String,Object> fact=validateFact(f);
                    if(!Set.of("automatic","explicit").contains(String.valueOf(fact.get("memoryMode"))))
                        throw new IllegalArgumentException("新写入仅支持自动分类或主动保存，旧字段仅兼容读取");
                    String category=text(values.get("category"),30);
                    if(!Set.of("profile","preference","relationship","event","goal","habit","note").contains(category)) throw new IllegalArgumentException("分类无效");
                    if("note".equals(category)&&!"explicit".equals(fact.get("memoryMode"))) throw new IllegalArgumentException("备注仅允许主动保存");
                    if("automatic".equals(fact.get("memoryMode"))&&!category.equals(fact.get("memoryField"))) throw new IllegalArgumentException("自动记忆分类不一致");
                    op.put("category",category); op.put("fact",fact); op.put("content",render(fact));
                    op.put("sessionId",text(body.getOrDefault("sessionId","unknown"),50));
                }
                ops.add(op);
            }
            return Result.ok(store.commit(user.getId(),role,revision,turn,ops));
        } catch(MemoryFactStore.Conflict e) { return Result.error(409,e.getMessage()); }
          catch(IllegalArgumentException e) { return Result.error(e.getMessage()); }
    }
    public static Map<String,Object> validateFact(Map<?,?> f) {
        Map<String,Object> result=new LinkedHashMap<>();
        for(String key:List.of("subject","predicate","value","temporalScope","timePrecision","timeValue","observedAt","sourceEvidence"))
            result.put(key,text(f.get(key),key.equals("value")?500:key.equals("sourceEvidence")?600:80));
        String scope=(String)result.get("temporalScope"), precision=(String)result.get("timePrecision"), value=(String)result.get("timeValue");
        if(f.containsKey("memoryMode")||f.containsKey("memoryField")) {
            String mode=text(f.get("memoryMode"),12), field=text(f.get("memoryField"),30);
            if(!("core".equals(mode)&&Set.of("name","age","student_status","grade","occupation","interest","like","dislike","communication","relationship","pet_kind","goal","habit","pet_name","pet_species","pet_relation").contains(field)
                ||"automatic".equals(mode)&&Set.of("profile","preference","relationship","goal","habit","event").contains(field)
                ||"explicit".equals(mode)&&"custom".equals(field))) throw new IllegalArgumentException("记忆策略字段无效");
            result.put("memoryMode",mode); result.put("memoryField",field);
        }
        for(String key:List.of("subject","predicate","value"))
            if(MemoryFactStore.normalized((String)result.get(key)).isEmpty()) throw new IllegalArgumentException("事实字段缺少有效内容");
        // 目录标识/别名随事实JSON保存；不再从展示名称推断同义属性是否相同。
        if(f.containsKey("entityId")||f.containsKey("predicateId")) {
            for(String key:List.of("entityId","predicateId")) {
                String id=text(f.get(key),64);
                if(!id.matches("[a-z][a-z0-9_]{1,63}")) throw new IllegalArgumentException("目录标识无效");
                result.put(key,id);
            }
            for(String key:List.of("canonicalPredicate","predicateDefinition","contextEvidence","contextMessageId"))
                if(f.containsKey(key)) result.put(key,text(f.get(key),key.equals("contextEvidence")?600:key.equals("predicateDefinition")?160:key.equals("canonicalPredicate")?48:64));
            if(result.containsKey("canonicalPredicate")&&!((String)result.get("canonicalPredicate")).matches("[a-z][a-z0-9_]{1,47}"))
                throw new IllegalArgumentException("属性代码无效");
            for(String key:List.of("entityAliases","predicateAliases")) result.put(key,strings(f.get(key),8,80));
        }
        if(!Set.of("current","historical","future").contains(scope)) throw new IllegalArgumentException("事实时间范围无效");
        LocalDate observed;
        try { observed=LocalDate.parse((String)result.get("observedAt")); } catch(Exception e) { throw new IllegalArgumentException("陈述日期无效"); }
        LocalDate start;
        try {
            start=switch(precision) {
                case "year" -> LocalDate.of(Integer.parseInt(value),1,1);
                case "month" -> LocalDate.parse(value+"-01");
                case "day" -> LocalDate.parse(value);
                case "approximate" -> null;
                default -> throw new IllegalArgumentException("时间精度无效");
            };
        } catch(Exception e) { throw new IllegalArgumentException("时间值无效"); }
        if("year".equals(precision)&&!value.matches("\\d{4}") || "month".equals(precision)&&!value.matches("\\d{4}-\\d{2}")) throw new IllegalArgumentException("时间格式无效");
        if("current".equals(scope)&&(!"day".equals(precision)||!value.equals(observed.toString()))) throw new IllegalArgumentException("当前状态必须带陈述日期");
        if("historical".equals(scope)&&start!=null&&start.isAfter(observed)) throw new IllegalArgumentException("历史事实不能发生在未来");
        if("future".equals(scope)&&start!=null && switch(precision) { case "year" -> start.getYear()<observed.getYear(); case "month" -> start.withDayOfMonth(1).isBefore(observed.withDayOfMonth(1)); default -> start.isBefore(observed); }) throw new IllegalArgumentException("未来计划时间无效");
        if("approximate".equals(precision)&&("current".equals(scope)||value.matches(".*(?:去年|前年|今年|明年|昨天|今天|上周|下周|上个月|下个月).*"))) throw new IllegalArgumentException("可换算的相对时间不能当作模糊时间");
        return result;
    }
    public static String render(Map<String,Object> f) {
        String subject="user".equals(f.get("subject"))?"用户":(String)f.get("subject");
        String time=(String)f.get("timeValue");
        if("year".equals(f.get("timePrecision"))) time+="年";
        String qualifier="current".equals(f.get("temporalScope"))?"截至"+time:"future".equals(f.get("temporalScope"))?"计划时间："+time:"历史时间："+time;
        return subject+"的"+f.get("predicate")+"："+f.get("value")+"（"+qualifier+"）";
    }
    private static long integer(Object v) {
        if(!(v instanceof Number n)||n.longValue()<0||n.doubleValue()!=n.longValue()) throw new IllegalArgumentException("版本或ID无效");
        return n.longValue();
    }
    private static String text(Object v,int cap) {
        if(!(v instanceof String s)||s.isBlank()||s.length()>cap) throw new IllegalArgumentException("字段为空或过长");
        return s.trim();
    }
    private static List<String> strings(Object value,int cap,int length) {
        if(value==null) return List.of();
        if(!(value instanceof List<?> list)||list.size()>cap) throw new IllegalArgumentException("检索参数无效");
        return list.stream().map(v->text(v,length)).distinct().toList();
    }
}
