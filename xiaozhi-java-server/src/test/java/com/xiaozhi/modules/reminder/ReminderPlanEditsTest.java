package com.xiaozhi.modules.reminder;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.transaction.annotation.Transactional;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class ReminderPlanEditsTest {
    final JdbcTemplate jdbc=mock(JdbcTemplate.class);
    final ReminderStore store=mock(ReminderStore.class);
    final ReminderPlans plans=new ReminderPlans(jdbc,store);
    final long now=at("2026-09-30T13:00:00"), original=at("2026-10-01T12:00:00"), adjusted=at("2026-10-01T14:00:00");
    long at(String value) { return OffsetDateTime.parse(value+"+08:00").toInstant().toEpochMilli(); }
    ReminderPlans.Plan plan(String status,long version) {
        return new ReminderPlans.Plan("plan","device","拿快递","daily",original,original,status,version,7,false,false,null,null);
    }
    void record(ReminderPlans.Plan value) {
        when(jdbc.query(contains("FOR UPDATE"),any(RowMapper.class),eq(7L),eq("plan"))).thenReturn(List.of(value));
    }
    String singleId() { return UUID.nameUUIDFromBytes("plan-to-once:plan:3".getBytes(StandardCharsets.UTF_8)).toString(); }
    String occurrenceId() { return UUID.nameUUIDFromBytes(("plan:"+original).getBytes(StandardCharsets.UTF_8)).toString(); }
    Reminder item(String id) {
        return new Reminder(id,7,"device","拿快递","scheduled",adjusted,adjusted,adjusted+ReminderStore.GRACE_MS,0,null,null,now,now,0);
    }
    @Test void conversionHasTransactionalBoundaryAndOneDeterministicReceipt() throws Exception {
        assertNotNull(ReminderPlans.class.getMethod("toOnce",long.class,String.class,String.class,long.class,long.class,long.class).getAnnotation(Transactional.class));
        record(plan("active",3));
        var saved=item(singleId());
        when(store.createAt(7,singleId(),"device","拿快递",adjusted,now)).thenReturn(saved);
        when(jdbc.update(anyString(),any(Object[].class))).thenReturn(1);
        assertSame(saved,plans.toOnce(7,"plan","device",3,adjusted,now));
        verify(jdbc).update(contains("status='cancelled',version=version+1"),eq("plan"),eq(7L),eq(3L));
        record(plan("cancelled",4));
        when(jdbc.queryForList(anyString(),eq(String.class),eq(singleId()),eq(7L))).thenReturn(List.of(singleId()));
        assertSame(saved,plans.toOnce(7,"plan","device",3,adjusted,now));
        verify(store,times(2)).createAt(7,singleId(),"device","拿快递",adjusted,now);
        verify(jdbc,times(1)).update(contains("status='cancelled'"),eq("plan"),eq(7L),eq(3L));
    }
    @Test void staleVersionAndWrongDeviceDoNotCreateReminder() {
        record(plan("active",4));
        assertThrows(ReminderStore.Conflict.class,()->plans.toOnce(7,"plan","device",3,adjusted,now));
        assertThrows(IllegalArgumentException.class,()->plans.toOnce(7,"plan","other",4,adjusted,now));
        verifyNoInteractions(store);
        verify(jdbc,never()).update(anyString(),any(Object[].class));
    }
    @Test void failedCancelThrowsToRollBackCreation() {
        record(plan("active",3));
        when(store.createAt(7,singleId(),"device","拿快递",adjusted,now)).thenReturn(item(singleId()));
        assertThrows(ReminderStore.Conflict.class,()->plans.toOnce(7,"plan","device",3,adjusted,now));
    }
    @Test void occurrenceEditKeepsPlanAnchorAndUsesSchedulersOccurrenceId() throws Exception {
        assertNotNull(ReminderPlans.class.getMethod("editOccurrence",long.class,String.class,String.class,long.class,long.class,long.class,long.class).getAnnotation(Transactional.class));
        record(plan("active",3));
        var saved=item(occurrenceId());
        when(store.get(7,occurrenceId())).thenReturn(saved);
        when(jdbc.update(anyString(),any(Object[].class))).thenReturn(1);
        assertSame(saved,plans.editOccurrence(7,"plan","device",3,original,adjusted,now));
        verify(store).createAt(7,occurrenceId(),"device","拿快递",adjusted,now);
        verify(jdbc).update(contains("SET plan_id=?,original_due_at=?"),eq("plan"),eq(original),eq(occurrenceId()),eq(7L));
        verify(jdbc).update(startsWith("UPDATE reminder_plan SET version=version+1"),eq("plan"),eq(7L),eq(3L));
        verify(jdbc,never()).update(contains("initial_at=?"),any(Object[].class));
    }
    @Test void occurrenceReplayDoesNotGenerateSecondItem() {
        record(plan("active",4));
        var saved=item(occurrenceId());
        when(jdbc.queryForList(anyString(),eq(String.class),eq(occurrenceId()),eq(7L),eq("plan"),eq(original))).thenReturn(List.of(occurrenceId()));
        when(store.createAt(7,occurrenceId(),"device","拿快递",adjusted,now)).thenReturn(saved);
        assertSame(saved,plans.editOccurrence(7,"plan","device",3,original,adjusted,now));
        verify(jdbc,never()).update(anyString(),any(Object[].class));
    }
    @Test void invalidOccurrenceAndPausedPlanCannotCreateItem() {
        record(plan("active",3));
        assertThrows(ReminderStore.Conflict.class,()->plans.editOccurrence(7,"plan","device",3,original+60000,adjusted,now));
        record(plan("paused",3));
        assertThrows(ReminderStore.Conflict.class,()->plans.editOccurrence(7,"plan","device",3,original,adjusted,now));
        verifyNoInteractions(store);
    }
    @Test void movedOccurrenceHistoryKeepsDeduplicationTombstone() {
        new ReminderStore(jdbc).pruneFinishedHistory(now);
        verify(jdbc).update(contains("history_deleted=true WHERE task_id IS NULL AND plan_id IS NOT NULL AND requested_at<>original_due_at"),eq(now-ReminderStore.FINISHED_RETENTION_MS));
        verify(jdbc).update(contains("DELETE FROM user_reminder WHERE task_id IS NULL AND (plan_id IS NULL OR requested_at=original_due_at)"),eq(now-ReminderStore.FINISHED_RETENTION_MS));
    }
    @Test void materializationPreservesExistingEditedOccurrence() {
        when(jdbc.queryForList(anyString(),eq(Long.class))).thenReturn(List.of(7L));
        when(jdbc.query(contains("ORDER BY next_at"),any(RowMapper.class),eq(7L),eq(original))).thenReturn(List.of(plan("active",4)));
        plans.materialize(original);
        verify(jdbc).update(contains("ON DUPLICATE KEY UPDATE id=id"),eq(occurrenceId()),eq(7L),eq("device"),eq("拿快递"),eq("scheduled"),eq(original),eq(original),eq(original+ReminderStore.GRACE_MS),eq(original),eq(original),eq("plan"),eq(original),eq(original),eq(original));
        verify(jdbc).update(contains("SET next_at=?,version=version+1"),eq(original+86400000L),eq("plan"));
    }
}
