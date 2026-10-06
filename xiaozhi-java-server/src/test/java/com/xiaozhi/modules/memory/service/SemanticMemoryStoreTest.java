package com.xiaozhi.modules.memory.service;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xiaozhi.modules.memory.dao.UserMemoryDao;
import com.xiaozhi.modules.memory.entity.UserMemoryEntity;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** 协议与写入边界的单元测试；事务回滚还需实际数据库集成验收。 */
class SemanticMemoryStoreTest {
    UserMemoryDao dao;
    MemoryFactStore scopes;
    JdbcTemplate jdbc;
    SemanticMemoryStore store;
    final String observed="2026-09-16T10:30:00+08:00";
    Map<String,Object> memory() { return new LinkedHashMap<>(Map.of("schemaVersion",3,"category","relationship","key","用户的猫数量","content","用户养有三只猫","status","active")); }
    Map<String,Object> add() { return Map.of("action","add","reason","new","memory",memory()); }
    Map<String,Integer> commit(long revision,List<?> ops) { return store.commit(7L,"default",revision,"turn","session","我有三只猫",observed,ops); }
    @BeforeEach void setup() {
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(new MybatisConfiguration(),"semantic-test"),UserMemoryEntity.class);
        dao=mock(UserMemoryDao.class); scopes=mock(MemoryFactStore.class); jdbc=mock(JdbcTemplate.class);
        store=new SemanticMemoryStore(dao,scopes,jdbc,new ObjectMapper());
        when(scopes.lock(7L,"default")).thenReturn(5L);
        when(jdbc.queryForList(anyString(),eq(String.class),eq(7L),eq("default"),eq("turn"))).thenReturn(List.of());
        when(dao.insert(any(UserMemoryEntity.class))).thenAnswer(call -> { call.getArgument(0,UserMemoryEntity.class).setId(11L); return 1; });
        when(dao.updateById(any(UserMemoryEntity.class))).thenReturn(1);
    }
    @Test void minimalContentNeedsNoEntityOrPredicate() {
        Map<String,Object> result=SemanticMemoryStore.validateMemory(memory(),"我有三只猫",observed);
        assertEquals("用户的猫数量",result.get("key"));
        assertFalse(result.containsKey("entityId")); assertFalse(result.containsKey("stateHistory"));
    }
    @Test void eventsWithSameTopicDoNotShareCurrentSlot() {
        Map<String,Object> event=memory(); event.put("category","event");
        assertNull(SemanticMemoryStore.slot(event));
        assertNotNull(SemanticMemoryStore.slot(memory()));
        event.put("category","goal"); event.put("status","closed");
        assertNull(SemanticMemoryStore.slot(event));
    }
    @Test void staleRevisionCannotMutate() {
        assertThrows(MemoryFactStore.Conflict.class,()->commit(4,List.of(add())));
        verifyNoInteractions(dao);
    }
    @Test void replayReturnsPriorCountsEvenWithStaleRevision() {
        when(jdbc.queryForList(anyString(),eq(String.class),eq(7L),eq("default"),eq("turn")))
            .thenReturn(List.of("{\"created\":1,\"updated\":0,\"deleted\":0}"));
        assertEquals(1,commit(0,List.of(add())).get("created"));
        verifyNoInteractions(dao);
    }
    @Test void newMemoryAndHistoryWrittenTogether() {
        assertEquals(1,commit(5,List.of(add())).get("created"));
        verify(dao).insert(argThat((UserMemoryEntity r)->r.getVersion()==1&&r.getFactJson().contains("schemaVersion")&&!r.getFactJson().contains("entityId")));
        verify(jdbc).update(startsWith("INSERT INTO user_memory_history"),eq(7L),eq("default"),eq(11L),isNull(),anyString(),eq("new"),eq("我有三只猫"),eq("turn"),eq(1),any());
        verify(scopes).advance(7L,"default");
    }
    @Test void exactCurrentTopicCannotBeAddedAgain() {
        UserMemoryEntity existing=new UserMemoryEntity(); existing.setId(9L);
        when(dao.selectOne(any())).thenReturn(existing);
        assertThrows(MemoryFactStore.Conflict.class,()->commit(5,List.of(add())));
        verify(dao,never()).insert(any(UserMemoryEntity.class));
    }
    @Test void legacyRecordCannotBeOverwritten() {
        UserMemoryEntity old=new UserMemoryEntity(); old.setId(11L); old.setVersion(1); old.setFactJson("{\"subject\":\"user\"}");
        when(dao.selectOne(any())).thenReturn(old);
        assertThrows(MemoryFactStore.Conflict.class,()->commit(5,List.of(Map.of("action","update","reason","corrected","targetId",11,"expectedVersion",1,"memory",memory()))));
        verify(dao,never()).updateById(any(UserMemoryEntity.class));
    }
    @Test void wrongTargetVersionCannotMutate() {
        UserMemoryEntity old=new UserMemoryEntity(); old.setId(11L); old.setVersion(2);
        when(dao.selectOne(any())).thenReturn(old);
        assertThrows(MemoryFactStore.Conflict.class,()->commit(5,List.of(Map.of("action","delete","reason","invalidated","targetId",11,"expectedVersion",1))));
        verify(dao,never()).updateById(any(UserMemoryEntity.class));
    }
    @Test void goalCompletionKeepsClosedGoalIdentity() {
        Map<String,Object> old=memory(),next=memory(); next.put("category","goal");
        assertThrows(MemoryFactStore.Conflict.class,()->SemanticMemoryStore.validateTransition(old,next,"completed"));
        old.put("category","goal");
        assertDoesNotThrow(()->SemanticMemoryStore.validateTransition(old,next,"completed"));
        old.put("status","closed");
        assertThrows(MemoryFactStore.Conflict.class,()->SemanticMemoryStore.validateTransition(old,next,"completed"));
    }
    @Test void topicWordingIsNotRecordIdentity() {
        Map<String,Object> next=memory(); next.put("key","用户饲养猫的数量");
        assertDoesNotThrow(()->SemanticMemoryStore.validateTransition(memory(),next,"corrected"));
    }
    @Test void completionWritesClosedGoalAndSeparateEvent() throws Exception {
        Map<String,Object> goal=memory(); goal.put("category","goal"); goal.put("key","用户六级目标"); goal.put("content","用户计划通过六级");
        UserMemoryEntity old=new UserMemoryEntity(); old.setId(7L); old.setVersion(1);
        old.setFactJson(new ObjectMapper().writeValueAsString(goal));
        when(dao.selectOne(any())).thenReturn(old);
        Map<String,Object> closed=new LinkedHashMap<>(goal); closed.put("content","用户已完成六级目标");
        Map<String,Object> event=new LinkedHashMap<>(closed); event.put("category","event"); event.put("key","用户通过六级经历");
        Map<String,Integer> result=commit(5,List.of(
            Map.of("action","update","reason","completed","targetId",7,"expectedVersion",1,"memory",closed),
            Map.of("action","add","reason","new","memory",event)));
        assertEquals(1,result.get("updated")); assertEquals(1,result.get("created"));
        verify(dao).updateById(argThat((UserMemoryEntity r)->r.getId()==7L&&r.getVersion()==2&&r.getFactJson().contains("closed")&&r.getCategory().equals("goal")));
        verify(dao).insert(argThat((UserMemoryEntity r)->r.getCategory().equals("event")&&r.getVersion()==1));
    }
    @Test void noteNeedsExplicitAuthorization() {
        Map<String,Object> note=memory(); note.put("category","note");
        assertThrows(IllegalArgumentException.class,()->commit(5,List.of(Map.of("action","add","reason","new","memory",note))));
    }
    @Test void invalidatedRecordCannotBeRestoredByAutomaticUpdate() {
        Map<String,Object> old=memory(); old.put("status","invalidated");
        assertThrows(MemoryFactStore.Conflict.class,()->SemanticMemoryStore.validateTransition(old,memory(),"corrected"));
    }
}
