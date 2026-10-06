package com.xiaozhi.modules.memory.controller;

import com.xiaozhi.modules.memory.dao.UserMemoryDao;
import com.xiaozhi.modules.memory.entity.UserMemoryEntity;
import com.xiaozhi.modules.memory.service.MemoryFactStore;
import com.xiaozhi.modules.memory.service.SemanticMemoryStore;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class MemoryControllerTest {
    MemoryController controller;
    UserMemoryDao dao;
    SemanticMemoryStore semantic;
    MemoryFactStore scopes;
    UserEntity user;
    @BeforeEach void setup() {
        controller=new MemoryController(); dao=mock(UserMemoryDao.class);
        semantic=mock(SemanticMemoryStore.class); scopes=mock(MemoryFactStore.class);
        ReflectionTestUtils.setField(controller,"memoryDao",dao);
        ReflectionTestUtils.setField(controller,"semanticStore",semantic);
        ReflectionTestUtils.setField(controller,"factStore",scopes);
        user=new UserEntity(); user.setId(7L);
    }
    @Test void manualCreationRequiresTheme() {
        assertNotEquals(0,controller.create(user,Map.of("category","preference","content","用户喜欢摄影")).getCode());
        verifyNoInteractions(semantic);
    }
    @Test void createsOnlySemanticMemory() {
        UserMemoryEntity row=new UserMemoryEntity(); row.setId(11L);
        when(semantic.manualCreate(7L,"default","preference","用户摄影偏好","用户喜欢摄影")).thenReturn(row);
        assertEquals(row,controller.create(user,Map.of("category","preference","key","用户摄影偏好","content","用户喜欢摄影")).getData());
        verify(dao,never()).insert(any(UserMemoryEntity.class));
    }
    @Test void editRequiresExpectedVersion() {
        UserMemoryEntity row=new UserMemoryEntity(); row.setId(11L); row.setVersion(2);
        when(dao.selectOne(any())).thenReturn(row);
        assertEquals(409,controller.update(user,11L,Map.of("content","用户21岁")).getCode());
        assertEquals(409,controller.update(user,11L,Map.of("version",1,"content","用户21岁")).getCode());
        verifyNoInteractions(semantic);
    }
    @Test void malformedOldDataIsRejectedWithoutPreview() {
        UserMemoryEntity row=new UserMemoryEntity();
        when(dao.selectList(any())).thenReturn(List.of(row));
        when(semantic.decode(row)).thenThrow(new MemoryFactStore.Conflict("记忆格式不符合当前协议"));
        assertEquals(409,controller.list(user,"default").getCode());
    }
    @Test void cannotDeleteAnotherUsersMemory() {
        assertEquals(404,controller.delete(user,99L).getCode());
        verify(dao,never()).deleteById(anyLong());
    }
    @Test void clearDoesNotNeedToDecodeOldData() {
        assertEquals(0,controller.clear(user,"default").getCode());
        verify(scopes).lock(7L,"default");
        verify(semantic).purgeHistory(7L,"default",null);
        verify(semantic,never()).decode(any());
        verify(dao).delete(any());
        verify(scopes).advance(7L,"default");
    }
}
