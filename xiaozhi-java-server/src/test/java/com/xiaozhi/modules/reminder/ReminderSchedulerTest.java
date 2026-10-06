package com.xiaozhi.modules.reminder;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.reactive.function.client.*;
import reactor.core.publisher.Mono;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class ReminderSchedulerTest {
    private ReminderScheduler scheduler(ReminderStore store,ExchangeFunction exchange) {
        var worker=new ReminderScheduler(store,WebClient.builder().exchangeFunction(exchange).build());
        ReflectionTestUtils.setField(worker,"baseUrl","http://localhost:8004");
        ReflectionTestUtils.setField(worker,"key","xiaozhi-reminders");
        return worker;
    }
    @Test void onlyConfirmedAudioResponseEntersAwaitingConfirmation() {
        var store=mock(ReminderStore.class); var item=ReminderStoreTest.item("scheduled",1);
        when(store.claim(eq(item),anyString(),anyLong())).thenReturn(true);
        var worker=scheduler(store,r->Mono.just(ClientResponse.create(HttpStatus.OK).header("Content-Type","application/json").body("{\"code\":0,\"outcome\":\"sent\"}").build()));
        worker.dispatch(item);
        verify(store).finish(eq(item),anyString(),eq("sent"),isNull(),anyLong());
    }
    @Test void timeoutCannotBeBlindlyRetried() {
        var store=mock(ReminderStore.class); var item=ReminderStoreTest.item("scheduled",1);
        when(store.claim(eq(item),anyString(),anyLong())).thenReturn(true);
        scheduler(store,r->Mono.error(new java.util.concurrent.TimeoutException())).dispatch(item);
        verify(store).finish(eq(item),anyString(),eq("unknown"),anyString(),anyLong());
    }
    @Test void unavailableServiceBeforeConnectionIsRetryable() {
        var store=mock(ReminderStore.class); var item=ReminderStoreTest.item("scheduled",1);
        when(store.claim(eq(item),anyString(),anyLong())).thenReturn(true);
        scheduler(store,r->Mono.error(new java.net.ConnectException())).dispatch(item);
        verify(store).finish(eq(item),anyString(),eq("retry"),anyString(),anyLong());
    }
    @Test void failedClaimNeverSends() {
        var store=mock(ReminderStore.class); var exchange=mock(ExchangeFunction.class);
        scheduler(store,exchange).dispatch(ReminderStoreTest.item("scheduled",1));
        verifyNoInteractions(exchange); verify(store,never()).finish(any(),anyString(),anyString(),any(),anyLong());
    }
    @Test void errorHttpStatusDoesNotCountAsSent() {
        var store=mock(ReminderStore.class); var item=ReminderStoreTest.item("scheduled",1);
        when(store.claim(eq(item),anyString(),anyLong())).thenReturn(true);
        scheduler(store,r->Mono.just(ClientResponse.create(HttpStatus.UNAUTHORIZED).header("Content-Type","application/json").body("{\"code\":0,\"outcome\":\"sent\"}").build())).dispatch(item);
        verify(store).finish(eq(item),anyString(),eq("unknown"),anyString(),anyLong());
    }

    static Reminder dueItem(String id,String device) {
        return new Reminder(id,7,device,id,"scheduled",1000,1000,1_801_000,1,null,null,0,0,60);
    }
    @Test void batchHasIndependentReceiptsIncludingMissingUnknownAndFailedClaims() {
        var store=mock(ReminderStore.class);
        var a=dueItem("a","device"); var b=dueItem("b","device"); var c=dueItem("c","device"); var changed=dueItem("changed","device");
        Map<String,String> attempts=new HashMap<>();
        when(store.claim(any(),anyString(),anyLong())).thenAnswer(invocation->{
            Reminder item=invocation.getArgument(0);
            if(item==changed) return false;
            attempts.put(item.id(),invocation.getArgument(1)); return true;
        });
        var exchange=mock(ExchangeFunction.class);
        when(exchange.exchange(any())).thenAnswer(ignored->Mono.just(ClientResponse.create(HttpStatus.OK)
            .header("Content-Type","application/json")
            .body("{\"code\":0,\"outcomes\":{\""+attempts.get("a")+"\":\"sent\",\""+attempts.get("b")+"\":\"retry\"}}")
            .build()));
        scheduler(store,exchange).dispatchBatch(List.of(a,b,c,changed));
        verify(exchange,times(1)).exchange(any());
        verify(store).finish(eq(a),eq(attempts.get("a")),eq("sent"),isNull(),anyLong());
        verify(store).finish(eq(b),eq(attempts.get("b")),eq("retry"),anyString(),anyLong());
        verify(store).finish(eq(c),eq(attempts.get("c")),eq("unknown"),anyString(),anyLong());
        verify(store,never()).finish(eq(changed),anyString(),anyString(),any(),anyLong());
        assertEquals(3,new HashSet<>(attempts.values()).size());
    }
    @Test void batchCannotCombineDifferentDevicesOrSendBeforeClaim() {
        var store=mock(ReminderStore.class); var exchange=mock(ExchangeFunction.class);
        scheduler(store,exchange).dispatchBatch(List.of(dueItem("a","one"),dueItem("b","two")));
        verifyNoInteractions(store,exchange);
    }
    @Test void failedReceiptSaveDoesNotPreventOtherMembersSaving() {
        var store=mock(ReminderStore.class); var a=dueItem("a","device"); var b=dueItem("b","device");
        when(store.claim(any(),anyString(),anyLong())).thenReturn(true);
        doThrow(new IllegalStateException()).when(store).finish(eq(a),anyString(),anyString(),any(),anyLong());
        scheduler(store,r->Mono.error(new java.net.ConnectException())).dispatchBatch(List.of(a,b));
        verify(store).finish(eq(b),anyString(),eq("retry"),anyString(),anyLong());
    }
}
