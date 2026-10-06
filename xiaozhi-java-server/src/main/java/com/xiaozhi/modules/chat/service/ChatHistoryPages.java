package com.xiaozhi.modules.chat.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.xiaozhi.modules.chat.dao.ChatHistoryDao;
import com.xiaozhi.modules.chat.entity.ChatHistoryEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;

/** create_date is a zone-less DATETIME storing Beijing wall time, not UTC.
 * Cursors are value boundaries, never row offsets or lookup-by-deleted-row.
 */
@Service
public class ChatHistoryPages {
    private final ChatHistoryDao dao;
    private final JdbcTemplate jdbc;
    public ChatHistoryPages(ChatHistoryDao dao,JdbcTemplate jdbc) { this.dao=dao; this.jdbc=jdbc; }
    private record Cursor(LocalDateTime time,long id) {}
    private String encode(Cursor c) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString((c.time()+"|"+c.id()).getBytes(StandardCharsets.UTF_8));
    }
    private Cursor decode(String value) {
        try {
            if(value.length()>160) throw new IllegalArgumentException();
            String[] parts=new String(Base64.getUrlDecoder().decode(value),StandardCharsets.UTF_8).split("\\|",-1);
            if(parts.length!=2) throw new IllegalArgumentException();
            Cursor c=new Cursor(LocalDateTime.parse(parts[0]),Long.parseLong(parts[1]));
            if(c.id()<0||c.time().getYear()<1000||c.time().getYear()>9999) throw new IllegalArgumentException();
            return c;
        } catch(RuntimeException e) { throw new IllegalArgumentException("历史游标无效，请重新加载会话"); }
    }
    private LambdaQueryWrapper<ChatHistoryEntity> base(String session,LocalDate date) {
        var q=new LambdaQueryWrapper<ChatHistoryEntity>().eq(ChatHistoryEntity::getSessionId,session)
            .isNotNull(ChatHistoryEntity::getCreateDate)
            .and(w->w.isNull(ChatHistoryEntity::getChatType).or().ne(ChatHistoryEntity::getChatType,"3"));
        if(date!=null) q.ge(ChatHistoryEntity::getCreateDate,date.atStartOfDay())
            .lt(ChatHistoryEntity::getCreateDate,date.plusDays(1).atStartOfDay());
        return q;
    }
    private LambdaQueryWrapper<ChatHistoryEntity> boundary(LambdaQueryWrapper<ChatHistoryEntity> q,Cursor c,boolean after) {
        return q.and(w->{
            if(after) w.gt(ChatHistoryEntity::getCreateDate,c.time()).or(v->v.eq(ChatHistoryEntity::getCreateDate,c.time()).gt(ChatHistoryEntity::getId,c.id()));
            else w.lt(ChatHistoryEntity::getCreateDate,c.time()).or(v->v.eq(ChatHistoryEntity::getCreateDate,c.time()).lt(ChatHistoryEntity::getId,c.id()));
        });
    }
    private Cursor cursor(ChatHistoryEntity row) { return new Cursor(row.getCreateDate(),row.getId()); }
    private boolean exists(String session,LocalDate date,Cursor c,boolean after) {
        return !dao.selectList(boundary(base(session,date),c,after).select(ChatHistoryEntity::getId).last("LIMIT 1")).isEmpty();
    }
    public Map<String,Object> page(String session,String before,String after,LocalDate date,int limit) {
        if(before!=null&&after!=null) throw new IllegalArgumentException("beforeCursor 和 afterCursor 不能同时使用");
        if(limit<1||limit>200) throw new IllegalArgumentException("每页数量应为1至200");
        boolean ascending=after!=null;
        Cursor input=before!=null?decode(before):after!=null?decode(after):null;
        var q=base(session,date);
        if(input!=null) boundary(q,input,ascending);
        q.select(ChatHistoryEntity::getId,ChatHistoryEntity::getSessionId,ChatHistoryEntity::getChatType,
            ChatHistoryEntity::getContent,ChatHistoryEntity::getCreateDate,ChatHistoryEntity::getReportTime);
        if(ascending) q.orderByAsc(ChatHistoryEntity::getCreateDate,ChatHistoryEntity::getId);
        else q.orderByDesc(ChatHistoryEntity::getCreateDate,ChatHistoryEntity::getId);
        var rows=new ArrayList<>(dao.selectList(q.last("LIMIT "+limit)));
        if(!ascending) Collections.reverse(rows);
        Cursor fallback=input!=null?input:new Cursor(date!=null?date.atStartOfDay():LocalDate.of(1000,1,1).atStartOfDay(),0);
        Cursor first=rows.isEmpty()?fallback:cursor(rows.get(0));
        Cursor last=rows.isEmpty()?fallback:cursor(rows.get(rows.size()-1));
        Map<String,Object> result=new HashMap<>();
        result.put("items",rows);
        result.put("nextBeforeCursor",encode(first));
        result.put("nextAfterCursor",encode(last));
        result.put("hasMoreBefore",exists(session,date,first,false));
        result.put("hasMoreAfter",exists(session,date,last,true));
        return result;
    }
    public Map<String,Object> dates(String session) {
        // The full session, not the latest loaded message page. Same wall-time
        // day boundaries as page(date) and deleteSession(date).
        var dates=jdbc.queryForList("SELECT DISTINCT DATE_FORMAT(create_date,'%Y-%m-%d') AS day FROM agent_chat_history WHERE session_id=? AND create_date IS NOT NULL AND (chat_type IS NULL OR chat_type<>'3') ORDER BY day",String.class,session);
        return Map.of("dates",dates,"timeZone","Asia/Shanghai");
    }
}
