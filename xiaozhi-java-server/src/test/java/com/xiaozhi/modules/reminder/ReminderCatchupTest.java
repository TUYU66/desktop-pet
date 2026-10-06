package com.xiaozhi.modules.reminder;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import java.util.List;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class ReminderCatchupTest {
    @Test void catchupIncludesPreviouslyAnnouncedButStillUnconfirmedReminders() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        long now=1_800_000_000_000L;
        when(jdbc.queryForObject(anyString(),eq(Long.class),any(Object[].class))).thenReturn(2L);
        store.catchupItems(7,"device",now);
        assertEquals(2,store.catchupCount(7,"device",now));
        verify(jdbc).query(argThat(sql->sql.contains("user_id=? AND device_id=?")
                &&sql.contains("history_deleted=false")&&sql.contains("'expired','missed'")
                &&!sql.contains("followup_stage<>")&&sql.contains("followup_stage>=?")&&sql.endsWith("LIMIT 3")),
            any(RowMapper.class),eq(7L),eq("device"),eq(now),eq(now-ReminderStore.FINISHED_RETENTION_MS),eq(-now));
    }

    @Test void playbackLeaseAndCommitAreAtomicAndDoNotModifyReminderSchedule() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        when(jdbc.update(anyString(),any(Object[].class))).thenReturn(1,0,1);
        long now=1_800_000_000_000L, stage=now*8+2;
        String token=UUID.randomUUID().toString();
        assertTrue(store.claimFollowup(7,"device","id",token,stage,now));
        assertFalse(store.claimFollowup(7,"device","id",UUID.randomUUID().toString(),stage,now));
        verify(jdbc).update(argThat(sql->sql.contains("AND followup_stage>=?")&&!sql.contains("followup_stage<>?")),eq(-(now+90_000)),eq(token),eq("id"),eq(7L),eq("device"),eq(now),eq(now-ReminderStore.FINISHED_RETENTION_MS),eq(stage),eq(-now));
        assertTrue(store.finishFollowup(7,"device","id",token,stage,now));
        verify(jdbc).update(argThat(sql->sql.startsWith("UPDATE user_reminder SET followup_stage=?,followup_token=NULL,version=version+1 WHERE")
                &&sql.contains("followup_token=?")&&sql.contains("followup_stage<?")),
            eq(stage),eq("id"),eq(7L),eq("device"),eq(token),eq(now),eq(now-ReminderStore.FINISHED_RETENTION_MS),eq(stage),eq(-now));
        store.releaseFollowup(7,"device","id",token);
        verify(jdbc).update(contains("followup_token=? AND followup_stage<0"),eq("id"),eq(7L),eq("device"),eq(token));
    }

    @Test void refreshTimeoutAndExpiryNeverDispatchAndRemainOwnerScoped() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        long now=1_800_000_000_000L;
        store.refreshCatchup(7,"device",now);
        verify(jdbc).update(contains("status='dispatching' AND updated_at<?"),eq(now),eq(7L),eq("device"),eq(now-240_000));
        verify(jdbc).update(argThat(sql->sql.contains("delivery_count>=3")&&sql.contains("ack_deadline+300000")
                &&sql.contains("user_id=? AND device_id=?")&&sql.contains("ack_deadline<=?")),eq(now),eq(7L),eq("device"),eq(now));
        verify(jdbc).update(contains("status='scheduled' AND expires_at<=?"),eq(now),eq(7L),eq("device"),eq(now));
        verifyNoMoreInteractions(jdbc);
    }

    @Test void expiredReminderCompletesOnlyThroughExplicitVersionCheckedConfirmation() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        var old=ReminderStoreTest.item("expired",2);
        when(jdbc.query(anyString(),any(RowMapper.class),eq(7L),eq("id"))).thenReturn(List.of(old));
        when(jdbc.update(anyString(),any(Object[].class))).thenReturn(1);
        store.act(7,"id",2,"confirm",0,2000);
        verify(jdbc).update(contains("version=? AND status=?"),eq("completed"),eq(1000L),eq(1000L),eq(1_801_000L),eq(old.scheduledAt()),eq(2000L),eq("id"),eq(7L),eq(2L),eq("expired"));
        assertThrows(ReminderStore.Conflict.class,()->store.act(7,"id",1,"confirm",0,2000));
    }
}
