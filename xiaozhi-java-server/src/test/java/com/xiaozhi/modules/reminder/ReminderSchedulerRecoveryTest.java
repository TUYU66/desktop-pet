package com.xiaozhi.modules.reminder;

import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.web.reactive.function.client.WebClient;
import java.sql.SQLException;
import java.util.List;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import static org.junit.jupiter.api.Assertions.*;

class ReminderSchedulerRecoveryTest {
    @Test void transientConnectionFailureDoesNotDisableNextScheduledInvocation() {
        var store=mock(ReminderStore.class);
        var scheduler=new ReminderScheduler(store,mock(WebClient.class));
        when(store.due(anyLong())).thenThrow(new DataAccessResourceFailureException("connection unavailable",new SQLException("closed","08003",0))).thenReturn(List.of());
        assertDoesNotThrow(scheduler::tick);
        assertDoesNotThrow(scheduler::tick);
        verify(store,times(2)).due(anyLong());
        verify(store,never()).claim(any(),anyString(),anyLong());
    }
    @Test void failedHistoryCleanupCanRecoverOnNextInvocation() {
        var store=mock(ReminderStore.class);
        var scheduler=new ReminderScheduler(store,mock(WebClient.class));
        when(store.pruneFinishedHistory(anyLong())).thenThrow(new DataAccessResourceFailureException("unavailable")).thenReturn(0);
        assertDoesNotThrow(scheduler::pruneFinishedHistory);
        assertDoesNotThrow(scheduler::pruneFinishedHistory);
        verify(store,times(2)).pruneFinishedHistory(anyLong());
    }
}
