package com.xiaozhi.modules.memory.controller;

import com.xiaozhi.modules.memory.service.MemoryFactStore;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class MemoryFactControllerTest {
    @Test void automaticNoteAndMismatchedCategoriesCannotBeCommitted() {
        var store=mock(MemoryFactStore.class); var controller=new MemoryFactController(store);
        Map<String,Object> f=fact(); f.put("memoryMode","automatic"); f.put("memoryField","habit");
        for(String category:List.of("note","profile")) {
            var body=Map.<String,Object>of("writeProtocolVersion",3,"turnId","turn","expectedRevision",0,"operations",List.of(Map.of("action","add","transition","new","category",category,"fact",f)));
            assertNotEquals(0,controller.commit(user(),body).getCode());
        }
        verifyNoInteractions(store);
        f.put("memoryField","note");
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
    }
    @Test void acceptsExpandedCoreFieldsButRejectsUnlistedFields() {
        for(String field:List.of("goal","habit","pet_name","pet_species","pet_relation")) {
            Map<String,Object> f=fact(); f.put("memoryMode","core"); f.put("memoryField",field);
            assertEquals(field,MemoryFactController.validateFact(f).get("memoryField"));
        }
        Map<String,Object> f=fact(); f.put("memoryMode","core"); f.put("memoryField","pet_weight");
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
    }
    private Map<String,Object> fact() { return new HashMap<>(Map.of("subject","user","predicate","年龄","value","21岁","temporalScope","current","timePrecision","day","timeValue","2026-09-13","observedAt","2026-09-13","sourceEvidence","我21岁")); }
    private UserEntity user() { UserEntity u=new UserEntity(); u.setId(7L); return u; }
    @Test void requiresAuthentication() {
        assertEquals(401,new MemoryFactController(mock(MemoryFactStore.class)).search(null,Map.of()).getCode());
    }
    @Test void controlledSearchPreservesOwnerStageAndSelectedTarget() {
        MemoryFactStore store=mock(MemoryFactStore.class);
        new MemoryFactController(store).search(user(),Map.of("controlledStage","resolve","ids",List.of(11),"keys",List.of("key"),"controlledQuery",""));
        verify(store).searchControlled(7L,"default",List.of("key"),List.of(),List.of(11L),"resolve","");
    }
    @Test void controlledSearchRejectsInvalidOrUnboundedRequests() {
        MemoryFactStore store=mock(MemoryFactStore.class);
        for(var body:List.of(Map.<String,Object>of("controlledStage","all"),
                Map.<String,Object>of("controlledStage","resolve","ids",Collections.nCopies(9,1)),
                Map.<String,Object>of("controlledStage","extract","controlledQuery","x".repeat(2401))))
            assertNotEquals(0,new MemoryFactController(store).search(user(),body).getCode());
        verifyNoInteractions(store);
    }
    @Test void rejectsWrongCurrentTimeAndFutureHistory() {
        Map<String,Object> f=fact(); f.put("timeValue","2025-09-13");
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
        f.put("temporalScope","historical"); f.put("timeValue","2027-09-13");
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
    }
    @Test void keepsHistoryAndUsesProgramRenderedContent() {
        MemoryFactStore store=mock(MemoryFactStore.class);
        when(store.commit(eq(7L),eq("default"),eq(5L),eq("turn"),anyList())).thenReturn(Map.of("created",1));
        var current=fact(); current.put("memoryMode","automatic"); current.put("memoryField","profile");
        var result=new MemoryFactController(store).commit(user(),Map.of("writeProtocolVersion",3,"turnId","turn","expectedRevision",5,"operations",List.of(Map.of("action","add","transition","new","fact",current,"category","profile","content","伪造正文"))));
        assertEquals(0,result.getCode());
        verify(store).commit(eq(7L),eq("default"),eq(5L),eq("turn"),argThat(ops->ops.get(0).get("content").equals("用户的年龄：21岁（截至2026-09-13）")));
    }
    @Test void rejectsOversizedBatchBeforeWriting() {
        MemoryFactStore store=mock(MemoryFactStore.class);
        assertNotEquals(0,new MemoryFactController(store).commit(user(),Map.of("writeProtocolVersion",3,"turnId","turn","expectedRevision",5,"operations",Collections.nCopies(9,Map.of()))).getCode());
        verifyNoInteractions(store);
    }
    @Test void returns409OnVersionConflict() {
        MemoryFactStore store=mock(MemoryFactStore.class);
        when(store.commit(eq(7L),eq("default"),eq(5L),eq("turn"),anyList())).thenThrow(new MemoryFactStore.Conflict("stale"));
        assertEquals(409,new MemoryFactController(store).commit(user(),Map.of("writeProtocolVersion",3,"turnId","turn","expectedRevision",5,"operations",List.of())).getCode());
    }
    @Test void persistsCanonicalCatalogAndContextProvenance() {
        Map<String,Object> f=fact();
        f.put("entityId","user"); f.put("predicateId","p_age"); f.put("canonicalPredicate","age");
        f.put("predicateDefinition","陈述时年龄，不推导生日"); f.put("predicateAliases",List.of("年龄","今年多大"));
        f.put("entityAliases",List.of("user")); f.put("contextEvidence","我说的年龄"); f.put("contextMessageId","previous-turn");
        Map<String,Object> checked=MemoryFactController.validateFact(f);
        assertEquals(f,checked);
    }
    @Test void rejectsPartialIdsAndInvalidCanonicalCode() {
        Map<String,Object> f=fact(); f.put("entityId","user");
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
        f.put("predicateId","p_age"); f.put("canonicalPredicate","购买-来源");
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
    }
    @Test void rejectsOversizedAliasDirectory() {
        Map<String,Object> f=fact(); f.put("entityId","user"); f.put("predicateId","p_age");
        f.put("predicateAliases",Collections.nCopies(9,"年龄"));
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
    }
    @Test void retainsControlledAndExplicitMemoryPolicy() {
        Map<String,Object> f=fact(); f.put("memoryMode","core"); f.put("memoryField","age");
        assertEquals(f,MemoryFactController.validateFact(f));
        f.put("memoryMode","explicit"); f.put("memoryField","custom");
        assertEquals(f,MemoryFactController.validateFact(f));
    }
    @Test void rejectsPartialAndUnknownMemoryPolicy() {
        Map<String,Object> f=fact(); f.put("memoryMode","core");
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
        f.put("memoryField","pet_count");
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
        f.put("memoryMode","explicit"); f.put("memoryField","age");
        assertThrows(IllegalArgumentException.class,()->MemoryFactController.validateFact(f));
    }
}
