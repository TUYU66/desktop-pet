package com.xiaozhi.modules.reminder;

import com.xiaozhi.modules.user.entity.UserEntity;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.scheduling.annotation.Scheduled;
import java.time.OffsetDateTime;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class ReminderOnlyTest {
    @Test void retiredTodoEndpointsNeverQueryOrWriteTheStores() throws Exception {
        var controller=new TaskController(); var user=new UserEntity(); user.setId(7L);
        assertEquals(410,controller.retired(user).block().getCode());
        assertEquals(401,controller.retired(null).block().getCode());
        assertNull(TaskStore.class.getAnnotation(org.springframework.stereotype.Service.class));
        assertNull(TaskStore.class.getMethod("clearDeadlineTasks").getAnnotation(Scheduled.class));
    }

    @Test void cancelledHistoryDoesNotConsumeActivePlanQuotaButHundredActivePlansDo() {
        var jdbc=mock(JdbcTemplate.class); var store=mock(ReminderStore.class);
        var plans=new ReminderPlans(jdbc,store);
        long now=OffsetDateTime.parse("2026-09-30T13:00:00+08:00").toInstant().toEpochMilli(), at=now+3600000;
        String id=UUID.randomUUID().toString();
        var history=new ArrayList<ReminderPlans.Plan>();
        for(int i=0;i<100;i++) history.add(new ReminderPlans.Plan("cancelled"+i,"device","旧提醒","daily",at,at,"cancelled",2,7,false,false,null,null));
        var saved=new ReminderPlans.Plan(id,"device","喝水","daily",at,at,"active",0,7,false,false,null,null);
        history.add(saved);
        when(jdbc.query(anyString(),any(RowMapper.class),eq(id),eq(7L))).thenReturn(List.of());
        when(jdbc.query(anyString(),any(RowMapper.class),eq(7L))).thenReturn(history);
        when(jdbc.queryForObject(contains("FROM device"),eq(Long.class),eq("device"))).thenReturn(1L);
        when(jdbc.queryForObject(contains("status IN ('active','paused')"),eq(Long.class),eq(7L))).thenReturn(0L,100L);
        assertSame(saved,plans.create(7,id,"device","喝水",at,"daily",now));
        assertThrows(IllegalArgumentException.class,()->plans.create(7,UUID.randomUUID().toString(),"device","喝水",at,"daily",now));
        verify(jdbc,times(1)).update(startsWith("INSERT INTO reminder_plan"),any(Object[].class));
    }

    @Test void explicitRescheduleWritesArrangedTimeWithoutChangingOccurrenceOrRequestIdentity() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        var old=new Reminder("id",7,"device","喝水","retry_pending",5000,5000,1805000,2,null,null,0,0,0,null,1,0,1000,null,1000,1000);
        when(jdbc.query(anyString(),any(RowMapper.class),eq(7L),eq("id"))).thenReturn(List.of(old));
        when(jdbc.update(anyString(),any(Object[].class))).thenReturn(1);
        store.reschedule(7,"id",2,"喝水",8000,2000);
        verify(jdbc).update(argThat(sql->sql.contains("scheduled_at=?")&&!sql.contains("original_due_at=?")&&!sql.contains("requested_at=?")),
            eq("喝水"),eq(8000L),eq(8000L),eq(1808000L),eq(8000L),eq(2000L),eq("id"),eq(7L),eq(2L));
    }

    @Test void automaticRetryKeepsArrangedTimeAndCannotAdvanceArchivedTodos() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        store.due(2000);
        verify(jdbc).update(argThat(sql->sql.contains("status='scheduled',due_at=next_attempt_at")&&sql.contains("task_id IS NULL")&&!sql.contains("scheduled_at=")),eq(2000L),eq(2000L));
        store.list(7); store.forDevice(7,"device");
        verify(jdbc).query(contains("task_id IS NULL"),any(RowMapper.class),eq(7L));
        verify(jdbc).query(contains("task_id IS NULL"),any(RowMapper.class),eq(7L),eq("device"));
    }
}
