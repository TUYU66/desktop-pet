package com.xiaozhi.modules.reminder;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.transaction.annotation.Transactional;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class ReminderStoreTest {
    @Test void finishedHistoryIsDeletedOnlyAfterSevenDays() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        long now=1_800_000_000_000L;
        store.pruneFinishedHistory(now);
        verify(jdbc).update(startsWith("DELETE FROM user_reminder"),
            eq(now-ReminderStore.FINISHED_RETENTION_MS));
    }
    @Test void historyDeletionChecksOwnerVersionAndTerminalStateAtomically() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        when(jdbc.update(anyString(),any(Object[].class))).thenReturn(1,0);
        store.deleteHistory(7,"id",2,3000);
        verify(jdbc).update(contains("WHERE user_id=? AND id=? AND version=? AND task_id IS NULL AND history_deleted=false AND status IN ('completed','cancelled','expired','missed')"),eq(3000L),eq(7L),eq("id"),eq(2L));
        assertThrows(ReminderStore.Conflict.class,()->store.deleteHistory(8,"id",2,3000));
    }
    @Test void deletedHistoryIsExcludedFromWebDeviceAndDirectLookup() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        store.list(7); store.forDevice(7,"device");
        assertThrows(IllegalArgumentException.class,()->store.get(7,"id"));
        verify(jdbc).query(contains("history_deleted=false"),any(RowMapper.class),eq(7L));
        verify(jdbc).query(contains("history_deleted=false"),any(RowMapper.class),eq(7L),eq("device"));
        verify(jdbc).query(contains("history_deleted=false"),any(RowMapper.class),eq(7L),eq("id"));
    }
    @Test void musicVolumeDefaultsAndWritesOnlyForOwnerWithinBounds() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        when(jdbc.queryForList(anyString(),eq(Integer.class),eq(7L))).thenReturn(List.of());
        assertEquals(30,store.musicVolume(7));
        assertThrows(IllegalArgumentException.class,()->store.setMusicVolume(7,101));
        assertThrows(IllegalArgumentException.class,()->store.setMusicVolume(7,-1));
        verify(jdbc,never()).update(anyString(),any(Object[].class));
        assertEquals(0,store.setMusicVolume(7,0));
        verify(jdbc).update(contains("reminder_preferences"),eq(7L),eq(0L),eq(0L));
    }
    @Test void absoluteCreateReplayUsesOriginalDateEvenAfterSnooze() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        String id=UUID.randomUUID().toString();
        var old=new Reminder(id,7,"device","喝水","scheduled",90000,90000,1890000,2,null,null,0,0,0,null,0,0,60000);
        when(jdbc.queryForList(anyString(),eq(Long.class))).thenReturn(List.of(7L));
        when(jdbc.query(anyString(),any(RowMapper.class),eq(id))).thenReturn(List.of(old));
        assertEquals(old,store.createAt(7,id,"device","喝水",60000,70000));
        assertThrows(ReminderStore.Conflict.class,()->store.createAt(7,id,"device","喝水",90000,70000));
        verify(jdbc,never()).update(anyString(),any(Object[].class));
    }
    static Reminder item(String status,long version) { return new Reminder("id",7,"device","喝水",status,1000,1000,1_801_000,version,"attempt",null,0,1000,60); }
    @Test void durationBoundsRejectInvalidValues() {
        assertEquals(10_000,ReminderStore.delayMillis(10));
        assertEquals(604800000,ReminderStore.delayMillis(604800));
        for(long seconds:new long[]{0,9,604801,Long.MAX_VALUE}) assertThrows(IllegalArgumentException.class,()->ReminderStore.delayMillis(seconds));
    }
    @Test void multipleAccountsAndWrongOwnerAreRejected() {
        JdbcTemplate jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        when(jdbc.queryForList(anyString(),eq(Long.class))).thenReturn(List.of(7L,8L),List.of(8L),List.of(7L));
        assertThrows(IllegalArgumentException.class,()->store.requireOwner(7));
        assertThrows(IllegalArgumentException.class,()->store.requireOwner(7));
        assertDoesNotThrow(()->store.requireOwner(7));
    }
    @Test void staleVersionAndDispatchingCannotBeMutated() {
        JdbcTemplate jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        when(jdbc.query(anyString(),any(RowMapper.class),eq(7L),eq("id"))).thenReturn(List.of(item("scheduled",2)),List.of(item("dispatching",2)));
        assertThrows(ReminderStore.Conflict.class,()->store.act(7,"id",1,"cancel",0,2000));
        assertThrows(ReminderStore.Conflict.class,()->store.act(7,"id",2,"cancel",0,2000));
        verify(jdbc,never()).update(anyString(),any(Object[].class));
    }
    @Test void confirmationAndSnoozeRequireDeliveredOrUnknownState() {
        JdbcTemplate jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        when(jdbc.query(anyString(),any(RowMapper.class),eq(7L),eq("id"))).thenReturn(List.of(item("scheduled",2)));
        assertThrows(ReminderStore.Conflict.class,()->store.act(7,"id",2,"confirm",0,2000));
        assertThrows(ReminderStore.Conflict.class,()->store.act(7,"id",2,"snooze",300,2000));
    }
    @Test void snoozeUsesServerTimeAndCompareAndSwap() {
        JdbcTemplate jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        when(jdbc.query(anyString(),any(RowMapper.class),eq(7L),eq("id"))).thenReturn(List.of(item("awaiting_confirmation",2)));
        when(jdbc.update(anyString(),any(Object[].class))).thenReturn(1);
        store.act(7,"id",2,"snooze",300,2000);
        verify(jdbc).update(contains("version=? AND status=?"),eq("scheduled"),eq(302000L),eq(302000L),eq(2102000L),eq(302000L),eq(2000L),eq("id"),eq(7L),eq(2L),eq("awaiting_confirmation"));
    }
    @Test void staleDispatchReceiptCannotOverwriteNewAttempt() {
        JdbcTemplate jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        store.finish(item("scheduled",2),"attempt","sent",null,2000);
        verify(jdbc).update(contains("task_id IS NULL AND status='dispatching' AND attempt_id=?"),eq("awaiting_confirmation"),isNull(),eq(17000L),eq(2000L),eq(62000L),eq(1),eq("id"),eq("attempt"));
    }
    @Test void recoveryMarksUncertainAndExpiresOverdueWithoutResending() {
        JdbcTemplate jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        when(jdbc.query(anyString(),any(RowMapper.class),eq(2_000_000L),eq(2_000_000L))).thenReturn(List.of());
        store.due(2_000_000);
        verify(jdbc).update(contains("status='delivery_unknown'"),eq(2_000_000L),eq(1_760_000L));
        verify(jdbc).update(contains("status='expired'"),eq(2_000_000L),eq(2_000_000L));
    }
    @Test void dueQueryIncludesOverlappingRemindersButNeverFutureAppointments() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        store.due(2_000_000);
        verify(jdbc).query(argThat(sql->sql.contains("r.next_attempt_at<=?")&&sql.contains("r.expires_at>?")
            &&sql.contains("r.history_deleted=false")&&!sql.contains("NOT EXISTS")
            &&sql.contains("ORDER BY r.next_attempt_at,r.id")),any(RowMapper.class),eq(2_000_000L),eq(2_000_000L));
    }
    @Test void replayedCreateReturnsExistingWithoutInserting() {
        JdbcTemplate jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        String id=UUID.randomUUID().toString(); var old=new Reminder(id,7,"device","喝水","scheduled",60000,60000,1860000,0,null,null,0,0,60);
        when(jdbc.queryForList(anyString(),eq(Long.class))).thenReturn(List.of(7L));
        when(jdbc.query(anyString(),any(RowMapper.class),eq(id))).thenReturn(List.of(old));
        assertEquals(old,store.create(7,id,"device","喝水",60,500));
        assertThrows(ReminderStore.Conflict.class,()->store.create(7,id,"device","喝水",120,500));
        verify(jdbc,never()).update(anyString(),any(Object[].class));
    }
    @Test void createLocksUserInTransaction() throws Exception {
        assertNotNull(ReminderStore.class.getMethod("create",long.class,String.class,String.class,String.class,long.class,long.class).getAnnotation(Transactional.class));
    }
}
