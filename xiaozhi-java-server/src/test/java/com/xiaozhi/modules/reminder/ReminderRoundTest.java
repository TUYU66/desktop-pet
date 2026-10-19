package com.xiaozhi.modules.reminder;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;

/** Execute real SQL against an isolated database; never connect to the user's DB. */
class ReminderRoundTest {
    JdbcTemplate jdbc;
    ReminderStore store;
    @BeforeEach void setup() {
        jdbc=new JdbcTemplate(new DriverManagerDataSource("jdbc:h2:mem:"+UUID.randomUUID()+";MODE=MySQL;DB_CLOSE_DELAY=-1","sa",""));
        jdbc.execute("CREATE TABLE user_reminder(id VARCHAR(64) PRIMARY KEY,user_id BIGINT,device_id VARCHAR(64),title VARCHAR(120),status VARCHAR(32),due_at BIGINT DEFAULT 1000,next_attempt_at BIGINT DEFAULT 1000,expires_at BIGINT DEFAULT 1801000,version BIGINT DEFAULT 0,attempt_id VARCHAR(64),last_error VARCHAR(200),created_at BIGINT DEFAULT 0,updated_at BIGINT DEFAULT 0,initial_seconds BIGINT DEFAULT 0,plan_id VARCHAR(64),delivery_count INT DEFAULT 0,ack_deadline BIGINT DEFAULT 0,requested_at BIGINT DEFAULT 1000,task_id VARCHAR(64),original_due_at BIGINT DEFAULT 1000,scheduled_at BIGINT DEFAULT 1000,round_started_at BIGINT DEFAULT 0,history_deleted BOOLEAN DEFAULT FALSE,followup_stage BIGINT DEFAULT 0,followup_token VARCHAR(64))");
        jdbc.update("INSERT INTO user_reminder(id,user_id,device_id,title,status) VALUES ('id',7,'device','喝水','scheduled')");
        store=new ReminderStore(jdbc);
    }
    Reminder item() { return store.get(7,"id"); }
    @Test void webAndHardwareShareOneWindowAndLateReceiptCannotExtendIt() {
        var web=store.start(7,"id",1000,null,2000);
        assertEquals(1,web.deliveryCount()); assertEquals(47000,web.ackDeadline());
        assertTrue(store.claim(web,"attempt",2100));
        assertEquals(47000,store.start(7,"id",1000,"attempt",9000).ackDeadline());
        store.finish(web,"attempt","sent",null,15000);
        assertEquals(1,item().deliveryCount()); assertEquals(47000,item().ackDeadline());
        assertEquals(47000,store.start(7,"id",1000,null,16000).ackDeadline());
    }
    @Test void threeFortyFiveSecondRoundsHaveTwoFiveMinuteWaitsThenExpire() {
        long start=2000;
        for(int round=1;round<=3;round++) {
            var current=store.start(7,"id",item().dueAt(),null,start);
            assertEquals(round,current.deliveryCount());
            store.due(start+44999); assertEquals("awaiting_confirmation",item().status());
            store.due(start+45000);
            if(round==3) { assertEquals("expired",item().status()); break; }
            assertEquals("retry_pending",item().status());
            assertEquals(start+345000,item().nextAttemptAt());
            store.due(start+344999); assertEquals("retry_pending",item().status());
            start+=345000; store.due(start); assertEquals("scheduled",item().status());
        }
        assertTrue(store.due(start+3600000).isEmpty());
        assertEquals(3,item().deliveryCount());
    }
    @Test void confirmationDuringSpeechWinsOverLateDeliveryReceiptAndDuplicateClick() {
        var before=item(); assertTrue(store.claim(before,"attempt",2000));
        var done=store.act(7,"id",item().version(),"confirm",300,2100);
        assertEquals("completed",done.status());
        store.finish(before,"attempt","sent",null,10000);
        assertEquals("completed",item().status());
        assertEquals(done,store.act(7,"id",0,"confirm",300,11000));
    }
    @Test void snoozeIsFreshCycleAndDuplicateOrOldRoundCannotMoveItAgain() {
        var old=store.start(7,"id",1000,null,2000);
        var later=store.act(7,"id",old.version(),"snooze",300,3000);
        assertEquals(303000,later.dueAt()); assertEquals(0,later.deliveryCount());
        assertEquals(0,later.ackDeadline()); assertEquals(0,later.roundStartedAt());
        assertThrows(ReminderStore.Conflict.class,()->store.act(7,"id",old.version(),"snooze",300,4000));
        assertThrows(ReminderStore.Conflict.class,()->store.start(7,"id",1000,null,5000));
    }
    @Test void offlineTransportRetriesDoNotConsumeNotificationRounds() {
        var before=item(); assertTrue(store.claim(before,"attempt",2000));
        store.finish(before,"attempt","retry","设备离线",3000);
        assertEquals(0,item().deliveryCount()); assertEquals(0,item().ackDeadline());
        store.due(1802000);
        assertEquals("delivery_unknown",item().status()); assertEquals(0,item().deliveryCount());
    }
    @Test void oldOfflineBacklogIsNotSpokenAsFreshNotificationsOnReconnect() {
        assertTrue(store.due(301001).isEmpty());
        assertEquals("delivery_unknown",item().status());
        assertEquals(0,item().deliveryCount());
        assertTrue(item().lastError().contains("未成功送达"));
    }
    @Test void voiceProcessingGraceIsBoundedAndAttemptScoped() {
        assertTrue(store.claim(item(),"attempt",2000));
        store.start(7,"id",1000,"attempt",2000);
        store.listening(7,"id","wrong",46000); assertEquals(47000,item().ackDeadline());
        store.listening(7,"id","attempt",46000); assertEquals(56000,item().ackDeadline());
        store.listening(7,"id","attempt",55000); assertEquals(57000,item().ackDeadline());
        store.listening(7,"id","attempt",56000); assertEquals(57000,item().ackDeadline());
    }
    @Test void notYetDueReminderCannotBeAcknowledgedOrStartedEarly() {
        assertThrows(ReminderStore.Conflict.class,()->store.act(7,"id",0,"confirm",300,999));
        assertThrows(ReminderStore.Conflict.class,()->store.start(7,"id",1000,null,999));
    }
    @Test void expiredRemindersRemainCountedAndCanBeConfirmedOrRearranged() {
        jdbc.update("UPDATE user_reminder SET status='expired',delivery_count=3");
        assertEquals(1,store.unanswered(7));
        var moved=store.act(7,"id",0,"snooze",300,2000);
        assertEquals("scheduled",moved.status()); assertEquals(0,moved.deliveryCount());
        assertEquals(302000,moved.dueAt());
    }
    @Test void unresolvedOverdueRemindersAreNotSilentlyPrunedAfterSevenDays() {
        jdbc.update("UPDATE user_reminder SET status='expired',delivery_count=3");
        store.pruneFinishedHistory(8*24*60*60_000L);
        assertEquals("expired",item().status()); assertEquals(1,store.unanswered(7));
        jdbc.update("UPDATE user_reminder SET status='completed'");
        store.pruneFinishedHistory(8*24*60*60_000L);
        assertThrows(IllegalArgumentException.class,()->item());
    }
}
