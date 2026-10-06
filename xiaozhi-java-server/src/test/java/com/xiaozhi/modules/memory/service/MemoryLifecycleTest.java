package com.xiaozhi.modules.memory.service;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MemoryLifecycleTest {
    private Map<String,Object> fact(String value) {
        return new HashMap<>(Map.of("entityId","user","predicateId","p_occupation","subject","user",
            "predicate","职业","value",value,"temporalScope","current","observedAt","2026-09-15",
            "timePrecision","day","timeValue","2026-09-15","sourceEvidence","我是"+value));
    }
    @Test void changedArchivesServerFactWhileCorrectedDoesNot() {
        var old=fact("学生"); var incoming=fact("工程师");
        incoming.put("stateHistory",List.of(Map.of("value","伪造历史")));
        var changed=MemoryFactStore.transitionFact(old,"profile",incoming,"profile","changed");
        var history=(List<?>)changed.get("stateHistory");
        assertEquals(1,history.size());
        var snapshot=(Map<?,?>)((Map<?,?>)history.get(0)).get("fact");
        assertEquals("学生",snapshot.get("value"));
        assertEquals("historical",snapshot.get("temporalScope"));
        assertEquals("current",old.get("temporalScope"));
        assertFalse(MemoryFactStore.transitionFact(old,"profile",incoming,"profile","corrected").containsKey("stateHistory"));
        assertFalse(MemoryFactStore.transitionFact(old,"profile",incoming,"profile","refines").containsKey("stateHistory"));
    }
    @Test void correctionKeepsPreviouslyValidHistory() {
        var original=fact("学生");
        var updated=MemoryFactStore.transitionFact(original,"profile",fact("工程师"),"profile","changed");
        var corrected=MemoryFactStore.transitionFact(updated,"profile",fact("实习工程师"),"profile","corrected");
        assertEquals(updated.get("stateHistory"),corrected.get("stateHistory"));
    }
    @Test void completedAndCancelledRemainHistoricalAndCannotCrossOwnerEntity() {
        var goal=fact("考过六级"); var ended=fact("考过六级"); ended.put("temporalScope","historical");
        goal.put("canonicalPredicate","exam_goal");
        var completed=new HashMap<>(ended);
        completed.put("canonicalPredicate","completed_exam_goal");
        completed.put("predicateId","p_"+MemoryFactStore.hash(MemoryFactStore.normalized("completed_exam_goal")).substring(0,40));
        assertEquals("completed",MemoryFactStore.transitionFact(goal,"goal",completed,"event","completed").get("goalStatus"));
        assertEquals("cancelled",MemoryFactStore.transitionFact(goal,"goal",ended,"goal","cancelled").get("goalStatus"));
        assertThrows(MemoryFactStore.Conflict.class,()->MemoryFactStore.transitionFact(goal,"profile",ended,"event","completed"));
        ended.put("entityId","e_other");
        assertThrows(MemoryFactStore.Conflict.class,()->MemoryFactStore.transitionFact(goal,"goal",ended,"event","completed"));
    }
}
