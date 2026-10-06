package com.xiaozhi.modules.memory.controller;

import com.xiaozhi.modules.memory.service.MemoryFactStore;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class MemoryProtocolV3Test {
    private Map<String,Object> operation() {
        Map<String,Object> f=new HashMap<>();
        f.put("subject","user"); f.put("predicate","本科专业"); f.put("value","自动化");
        f.put("temporalScope","current"); f.put("timePrecision","day"); f.put("timeValue","2026-09-15");
        f.put("observedAt","2026-09-15"); f.put("sourceEvidence","我本科读的是自动化");
        f.put("memoryMode","automatic"); f.put("memoryField","profile");
        return Map.of("action","add","transition","new","category","profile","fact",f);
    }
    private Map<String,Object> body(int count) {
        return new HashMap<>(Map.of("writeProtocolVersion",3,"roleId","default","sessionId","test",
            "turnId","turn","expectedRevision",0,"operations",Collections.nCopies(count,operation())));
    }
    @Test void eightOperationsReachStoreButNineDoNot() {
        var store=mock(MemoryFactStore.class); var controller=new MemoryFactController(store);
        var user=new UserEntity(); user.setId(7L);
        when(store.commit(anyLong(),anyString(),anyLong(),anyString(),anyList())).thenReturn(Map.of("created",8,"updated",0,"deleted",0));
        controller.commit(user,body(8));
        verify(store).commit(eq(7L),eq("default"),eq(0L),eq("turn"),argThat(ops->ops.size()==8));
        clearInvocations(store);
        controller.commit(user,body(9)); verifyNoInteractions(store);
    }
    @Test void oldProtocolAndOldFieldWritesAreRejectedBeforeStore() {
        var store=mock(MemoryFactStore.class); var controller=new MemoryFactController(store);
        var user=new UserEntity(); user.setId(7L);
        var old=body(1); old.remove("writeProtocolVersion"); controller.commit(user,old);
        var request=body(1); var op=new HashMap<>(operation());
        @SuppressWarnings("unchecked") var fact=new HashMap<>((Map<String,Object>)op.get("fact"));
        fact.put("memoryMode","core"); fact.put("memoryField","age");
        op.put("fact",fact); request.put("operations",List.of(op)); controller.commit(user,request);
        verifyNoInteractions(store);
    }
}
