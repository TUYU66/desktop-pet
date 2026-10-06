package com.xiaozhi.modules.reminder;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import java.time.OffsetDateTime;

class ReminderPlansTest {
    long at(String value) { return OffsetDateTime.parse(value+"+08:00").toInstant().toEpochMilli(); }
    @Test void dailySurvivesMonthBoundary() {
        assertEquals(at("2026-10-01T23:00:00"),ReminderPlans.next(at("2026-09-30T23:00:00"),"daily",at("2026-09-30T23:01:00")));
    }
    @Test void weeklyKeepsOriginalWeekdayAfterLongOutage() {
        assertEquals(at("2026-10-02T20:00:00"),ReminderPlans.next(at("2026-09-18T20:00:00"),"weekly",at("2026-09-29T23:01:00")));
    }
    @Test void weekdaysSkipWeekend() {
        assertEquals(at("2026-09-21T08:00:00"),ReminderPlans.next(at("2026-09-18T08:00:00"),"weekdays",at("2026-09-18T08:01:00")));
    }
    @Test void resumeBeforeFirstOccurrenceDoesNotAdvanceBeforeAnchor() {
        assertEquals(at("2026-09-18T08:00:00"),ReminderPlans.next(at("2026-09-18T08:00:00"),"weekly",at("2026-09-14T08:00:00")));
        assertEquals(at("2027-09-18T08:00:00"),ReminderPlans.next(at("2027-09-18T08:00:00"),"weekly",at("2026-09-14T08:00:00")));
    }
    @Test void pastAndUnboundedDatesRejected() {
        assertThrows(IllegalArgumentException.class,()->ReminderStore.validateDate(100,100));
        assertThrows(IllegalArgumentException.class,()->ReminderStore.validateDate(Long.MAX_VALUE,100));
        assertThrows(IllegalArgumentException.class,()->ReminderPlans.next(100,"model-invented-rule",100));
    }
    @Test void confirmationDeadlineRecoveryAndRetryAreSeparateFromTransportFailure() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        store.due(1000);
        verify(jdbc).update(contains("delivery_count>=3"),eq(1000L),eq(1000L));
        verify(jdbc).update(contains("status='retry_pending' AND next_attempt_at<=?"),eq(1000L),eq(1000L));
    }
    @Test void wakingDoesNotConfirmAndIsBoundToAttempt() {
        var jdbc=mock(JdbcTemplate.class); var store=new ReminderStore(jdbc);
        store.listening(7,"id","attempt",2000);
        verify(jdbc).update(contains("attempt_id=? AND task_id IS NULL AND status='awaiting_confirmation'"),eq(32000L),eq("id"),eq(7L),eq("attempt"),eq(2000L),eq(32000L));
    }
}
