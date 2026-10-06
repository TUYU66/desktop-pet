package com.xiaozhi.modules.reminder;

import org.junit.jupiter.api.Test;
import com.xiaozhi.modules.user.entity.UserEntity;
import liquibase.changelog.ChangeLogParameters;
import liquibase.parser.ChangeLogParserFactory;
import liquibase.resource.ClassLoaderResourceAccessor;
import java.time.Duration;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class ReminderControllerTest {
    private UserEntity user() { var user=new UserEntity(); user.setId(7L); return user; }
    @Test void unauthenticatedRequestNeverReadsStore() {
        var store=mock(ReminderStore.class);
        assertEquals(401,new ReminderController(store).list(null).block().getCode());
        verifyNoInteractions(store);
    }
    @Test void fractionalTimeNeverCreatesReminder() {
        var store=mock(ReminderStore.class);
        var result=new ReminderController(store).create(user(),Map.of("requestId",UUID.randomUUID().toString(),"title","喝水","deviceId","device","seconds",10.5)).block(Duration.ofSeconds(2));
        assertNotEquals(0,result.getCode());
        verify(store,never()).create(anyLong(),anyString(),anyString(),anyString(),anyLong(),anyLong());
    }
    @Test void mutationUsesAuthenticatedOwnerAndReportsConflict() {
        var store=mock(ReminderStore.class);
        when(store.act(eq(7L),eq("id"),eq(3L),eq("confirm"),eq(300L),anyLong())).thenThrow(new ReminderStore.Conflict("changed"));
        var result=new ReminderController(store).act(user(),"id",Map.of("version",3,"action","confirm","userId",99)).block(Duration.ofSeconds(2));
        assertEquals(409,result.getCode());
    }
    @Test void migrationParsesWithExistingChangelogWithoutDatabase() throws Exception {
        try(var resources=new ClassLoaderResourceAccessor()) {
            String path="db/changelog/db.changelog-master.xml";
            var changelog=ChangeLogParserFactory.getInstance().getParser(path,resources).parse(path,new ChangeLogParameters(),resources);
            assertTrue(changelog.getChangeSets().stream().anyMatch(c->c.getId().equals("20260914-reminders")));
            assertTrue(changelog.getChangeSets().stream().anyMatch(c->c.getId().equals("20260914-schedules")));
        }
    }
    @Test void deployedScheduleChecksumsRemainValidAndNewColumnIsSeparate() throws Exception {
        try(var resources=new ClassLoaderResourceAccessor()) {
            String path="db/changelog/db.changelog-master.xml";
            var changelog=ChangeLogParserFactory.getInstance().getParser(path,resources).parse(path,new ChangeLogParameters(),resources);
            var original=changelog.getChangeSets().stream().filter(c->c.getId().equals("20260914-schedules")).findFirst().orElseThrow();
            assertEquals("9:73d5541a504ebebdebc50603688a40e1",original.generateCheckSum(liquibase.ChecksumVersion.V9).toString());
            assertTrue(original.isCheckSumValid(liquibase.change.CheckSum.parse("9:73d5541a504ebebdebc50603688a40e1")));
            assertTrue(original.isCheckSumValid(liquibase.change.CheckSum.parse("9:740345d6103d87ef2850d540e9231e4e")));
            assertFalse(original.isCheckSumValid(liquibase.change.CheckSum.parse("9:00000000000000000000000000000000")));
            var addition=changelog.getChangeSets().stream().filter(c->c.getId().equals("20260914-reminder-requested-at")).findFirst().orElseThrow();
            assertEquals(liquibase.precondition.core.PreconditionContainer.FailOption.MARK_RAN,addition.getPreconditions().getOnFail());
            var column=(liquibase.change.core.AddColumnChange)addition.getChanges().get(0);
            assertEquals("requested_at",column.getColumns().get(0).getName());
            assertTrue(changelog.getChangeSets().stream().anyMatch(c->c.getId().equals("20260914-reminder-requested-at-backfill")));
        }
    }
}
