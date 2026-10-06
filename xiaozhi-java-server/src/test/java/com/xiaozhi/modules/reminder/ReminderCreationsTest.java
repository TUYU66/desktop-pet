package com.xiaozhi.modules.reminder;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class ReminderCreationsTest {
    final JdbcTemplate jdbc=mock(JdbcTemplate.class);
    final ReminderStore store=mock(ReminderStore.class);
    final ReminderPlans plans=mock(ReminderPlans.class);
    final ReminderCreations receipts=new ReminderCreations(jdbc,store,plans);
    final String id=UUID.randomUUID().toString();
    Map<String,Object> original(String rule) {
        var value=new HashMap<String,Object>();
        value.put("user_id",7L); value.put("device_id","device"); value.put("title","浇花");
        value.put("trigger_at",1000L); value.put("recurrence",rule); value.put("seconds",null);
        return value;
    }
    @Test void completedEditedOrPrunedRecordStillConfirmsOriginalWithoutCreatingAgain() {
        when(jdbc.queryForList(contains("FROM reminder_creation"),eq(id))).thenReturn(List.of(original("once")));
        when(jdbc.queryForList(contains("FROM user_reminder"),eq(id),eq(7L))).thenReturn(
            List.of(Map.of("status","completed","title","已修改的标题","dueAt",9000L)),List.of());
        var first=receipts.create(7,id,"device","浇花",1000L,"once",null,5000);
        assertEquals("completed",((Map<?,?>)first.get("current")).get("status"));
        assertEquals("浇花",((Map<?,?>)first.get("creation")).get("title"));
        assertEquals(1000L,((Map<?,?>)first.get("creation")).get("triggerAt"));
        var second=receipts.create(7,id,"device","浇花",1000L,"once",null,6000);
        assertEquals("history_removed",((Map<?,?>)second.get("current")).get("status"));
        verify(store,never()).createAt(anyLong(),anyString(),anyString(),anyString(),anyLong(),anyLong());
        verify(jdbc,never()).update(anyString(),any(Object[].class));
    }
    @Test void pausedAndRetimedPlanReceiptUsesOriginalRuleAndTime() {
        when(jdbc.queryForList(contains("FROM reminder_creation"),eq(id))).thenReturn(List.of(original("daily")));
        when(jdbc.queryForList(contains("FROM reminder_plan"),eq(id),eq(7L))).thenReturn(List.of(Map.of("status","paused","initialAt",9000L,"recurrence","weekly")));
        var result=receipts.create(7,id,"device","浇花",1000L,"daily",null,5000);
        assertEquals("paused",((Map<?,?>)result.get("current")).get("status"));
        assertEquals("daily",((Map<?,?>)result.get("creation")).get("recurrence"));
        verifyNoInteractions(plans);
    }
    @Test void otherOwnerDeviceTitleTimeOrRuleCannotReuseReceipt() {
        when(jdbc.queryForList(contains("FROM reminder_creation"),eq(id))).thenReturn(List.of(original("once")));
        assertThrows(ReminderStore.Conflict.class,()->receipts.create(8,id,"device","浇花",1000L,"once",null,5000));
        assertThrows(ReminderStore.Conflict.class,()->receipts.create(7,id,"other","浇花",1000L,"once",null,5000));
        assertThrows(ReminderStore.Conflict.class,()->receipts.create(7,id,"device","喝水",1000L,"once",null,5000));
        assertThrows(ReminderStore.Conflict.class,()->receipts.create(7,id,"device","浇花",2000L,"once",null,5000));
        assertThrows(ReminderStore.Conflict.class,()->receipts.create(7,id,"device","浇花",1000L,"daily",null,5000));
        verify(jdbc,never()).update(anyString(),any(Object[].class));
    }
    @Test void firstCreationAndReceiptAreInSameTransaction() throws Exception {
        assertNotNull(ReminderCreations.class.getMethod("create",long.class,String.class,String.class,String.class,Long.class,String.class,Long.class,long.class).getAnnotation(org.springframework.transaction.annotation.Transactional.class));
        when(jdbc.queryForList(contains("FROM reminder_creation"),eq(id))).thenReturn(List.of());
        receipts.create(7,id,"device","浇花",1000L,"once",null,500);
        verify(store).createAt(7,id,"device","浇花",1000,500);
        verify(jdbc).update(contains("INSERT INTO reminder_creation"),eq(id),eq(7L),eq("device"),eq("浇花"),eq(1000L),eq("once"),isNull(),eq(500L));
    }
}
