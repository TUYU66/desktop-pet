package com.xiaozhi.modules.memory.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import com.xiaozhi.modules.memory.dao.UserMemoryDao;
import com.xiaozhi.modules.memory.entity.UserMemoryEntity;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class MemoryFactStoreTest {
    private UserMemoryDao dao;
    private JdbcTemplate jdbc;
    private MemoryFactStore store;
    private final Map<String,Object> fact=Map.of("subject","user","predicate","年龄","value","21岁","temporalScope","current","timeValue","2026-09-13");
    private Map<String,Object> add() { return Map.of("action","add","transition","new","fact",fact,"content","用户的年龄：21岁","category","profile","sessionId","s"); }
    @BeforeEach void setup() {
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(new MybatisConfiguration(),"test"),UserMemoryEntity.class);
        dao=mock(UserMemoryDao.class); jdbc=mock(JdbcTemplate.class);
        store=new MemoryFactStore(dao,jdbc,new ObjectMapper());
        when(jdbc.queryForObject(anyString(),eq(Long.class),eq(7L),eq("default"))).thenReturn(5L);
        when(jdbc.queryForList(anyString(),eq(String.class),eq(7L),eq("default"),anyString())).thenReturn(List.of());
        when(dao.insert(any(UserMemoryEntity.class))).thenReturn(1);
    }
    @Test void identityAndSlotSeparateHistoryFromCurrent() {
        Map<String,Object> past=new HashMap<>(fact); past.put("temporalScope","historical"); past.put("timeValue","2025");
        assertEquals(MemoryFactStore.identity(fact),MemoryFactStore.identity(past));
        assertNotEquals(MemoryFactStore.slot(fact),MemoryFactStore.slot(past));
        Map<String,Object> later=new HashMap<>(fact); later.put("timeValue","2026-09-14");
        assertEquals(MemoryFactStore.slot(fact),MemoryFactStore.slot(later));
    }
    @Test void stableIdsOverrideSynonymousDisplayNames() {
        Map<String,Object> canonical=new HashMap<>(fact); canonical.put("entityId","user"); canonical.put("predicateId","p_age");
        Map<String,Object> alias=new HashMap<>(canonical); alias.put("predicate","今年多大");
        assertEquals(MemoryFactStore.hash("user\u001fp_age"),MemoryFactStore.identity(canonical));
        assertEquals(MemoryFactStore.identity(canonical),MemoryFactStore.identity(alias));
        assertEquals(MemoryFactStore.slot(canonical),MemoryFactStore.slot(alias));
    }
    @Test void historicalSlotIncludesTimePrecision() {
        Map<String,Object> year=new HashMap<>(fact); year.put("temporalScope","historical"); year.put("timePrecision","year"); year.put("timeValue","2025");
        Map<String,Object> vague=new HashMap<>(year); vague.put("timePrecision","approximate");
        assertNotEquals(MemoryFactStore.slot(year),MemoryFactStore.slot(vague));
    }
    @Test void staleSnapshotCannotWrite() {
        assertThrows(MemoryFactStore.Conflict.class,()->store.commit(7L,"default",4L,"turn",List.of(add())));
        verifyNoInteractions(dao);
    }
    @Test void replayReturnsSavedResultBeforeCheckingOldRevision() {
        when(jdbc.queryForList(anyString(),eq(String.class),eq(7L),eq("default"),eq("turn"))).thenReturn(List.of("{\"created\":1,\"updated\":0,\"deleted\":0}"));
        assertEquals(1,store.commit(7L,"default",0L,"turn",List.of(add())).get("created"));
        verifyNoInteractions(dao);
    }
    @Test void addPersistsStructureAndAdvancesRevision() {
        assertEquals(1,store.commit(7L,"default",5L,"turn",List.of(add())).get("created"));
        verify(dao).insert(argThat((UserMemoryEntity m)->m.getUserId()==7L && m.getVersion()==0 && m.getFactJson().contains("年龄") && "turn".equals(m.getSourceTurnId())));
        verify(jdbc).update("UPDATE user_memory_scope SET revision=revision+1 WHERE user_id=? AND role_id=?",7L,"default");
    }
    @Test void existingStateCannotBeOverwrittenThroughAdd() {
        UserMemoryEntity existing=new UserMemoryEntity(); existing.setId(11L); existing.setContent("用户的年龄：20岁");
        when(dao.selectOne(any())).thenReturn(existing);
        assertThrows(MemoryFactStore.Conflict.class,()->store.commit(7L,"default",5L,"turn",List.of(add())));
        verify(dao,never()).insert(any(UserMemoryEntity.class));
    }
    @Test void matchingDuplicateIsNoOp() {
        UserMemoryEntity existing=new UserMemoryEntity(); existing.setId(11L); existing.setContent("用户的年龄：21岁");
        when(dao.selectOne(any())).thenReturn(existing);
        assertEquals(0,store.commit(7L,"default",5L,"turn",List.of(add())).get("created"));
        verify(dao,never()).insert(any(UserMemoryEntity.class));
    }
    @Test void missingOwnerScopedTargetCannotBeDeleted() {
        assertThrows(MemoryFactStore.Conflict.class,()->store.commit(7L,"default",5L,"turn",List.of(Map.of("action","delete","id",11L,"expectedVersion",0))));
        verify(dao,never()).deleteById(anyLong());
    }
    @Test void staleTargetVersionCannotBeDeleted() {
        UserMemoryEntity existing=new UserMemoryEntity(); existing.setId(11L); existing.setVersion(3);
        when(dao.selectOne(any())).thenReturn(existing);
        assertThrows(MemoryFactStore.Conflict.class,()->store.commit(7L,"default",5L,"turn",List.of(Map.of("action","delete","id",11L,"expectedVersion",2))));
        verify(dao,never()).deleteById(anyLong());
    }
    @Test void wholeCommitUsesRollbackTransaction() throws Exception {
        // 真正的 MySQL 并发/回滚仍需部署后集成验收，此处防止事务声明被遗漏。
        assertNotNull(MemoryFactStore.class.getMethod("commit",Long.class,String.class,long.class,String.class,List.class).getAnnotation(Transactional.class));
    }

    @Test void controlledExtractionSearchesAttributesAndAliasesAndReportsTruncation() {
        List<UserMemoryEntity> rows=new ArrayList<>();
        for(long i=0;i<61;i++) { UserMemoryEntity m=new UserMemoryEntity(); m.setId(i); rows.add(m); }
        when(dao.selectList(any())).thenReturn(rows);
        var result=store.searchControlled(7L,"default",List.of(),List.of("摄影"),List.of(),"extract","我又养一只猫");
        assertEquals(60,((List<?>)result.get("memories")).size()); assertEquals(true,result.get("truncated"));
        assertEquals(1,result.get("retrievalVersion"));
        verify(dao).selectList(argThat(w->{ String sql=w.getSqlSegment();
            return sql.contains("user_id")&&sql.contains("role_id")&&sql.contains("LOCATE")&&sql.contains("content LIKE")&&sql.contains("fact_json LIKE")&&sql.contains("LIMIT 61"); }));
    }

    @Test void controlledResolutionUsesExactSlotsAndScopedIdsOnly() {
        when(dao.selectList(any())).thenReturn(List.of());
        store.searchControlled(7L,"default",List.of("key"),List.of("ignored"),List.of(11L),"resolve","");
        verify(dao).selectList(argThat(w->{ String sql=w.getSqlSegment();
            return sql.contains("user_id")&&sql.contains("role_id")&&sql.contains("slot_key IN")&&sql.contains("id IN")&&!sql.contains("LIKE"); }));
    }

    @Test void emptyResolutionDoesNotFallBackToAllMemories() {
        assertEquals(List.of(),store.searchControlled(7L,"default",List.of(),List.of(),List.of(),"resolve","").get("memories"));
        verifyNoInteractions(dao);
    }
}
