"""Routing ownership and stable creation recovery; source only, no live services."""
import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from core.reminders.conversation import respond
from core.reminders.parser import ZONE
from core.reminders.receipts import verified
from core.reminders.client import OperationRejected


def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=ZONE).timestamp()*1000)


NOW=ms('2026-09-30T13:00:00')


def receipt(device,body,status=None):
    return dict(id=body['requestId'],creation=dict(requestId=body['requestId'],deviceId=device,
        title=body['title'],triggerAt=body['triggerAt'],recurrence=body['recurrence'],seconds=None),
        current=dict(id=body['requestId'],title=body['title'],status=status or ('scheduled' if body['recurrence']=='once' else 'active'),
        dueAt=body['triggerAt']+300000,scheduledAt=body['triggerAt']))


class RoutingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn=NS(headers={'device-id':'device'},session_id='session',logger=Mock())
        self.snapshot=dict(items=[],plans=[],serverNow=NOW)
        self.command=AsyncMock(side_effect=receipt)
        self.state=AsyncMock(return_value=self.snapshot)
        for name,mock in [('state',self.state),('command',self.command)]:
            p=patch('core.reminders.client.'+name,mock); p.start(); self.addCleanup(p.stop)

    async def test_single_question_does_not_intercept_ack_and_keeps_draft(self):
        await respond(self.conn,'明天提醒我浇花')
        draft=self.conn.schedule_pending
        item=dict(id='water',title='喝水',status='awaiting_confirmation',version=2,dueAt=NOW)
        self.snapshot['items']=[item]
        self.command.side_effect=lambda device,body:dict(item,status='completed',version=3)
        with patch('core.device.face.send_face',AsyncMock()):
            self.assertIn('记为收到了',await respond(self.conn,'收到'))
        self.assertEqual(dict(action='confirm',id='water',version=2),self.command.call_args.args[1])
        self.assertIs(draft,self.conn.schedule_pending)
        self.command.side_effect=receipt
        self.assertIn('浇花',await respond(self.conn,'下午三点'))
        self.assertEqual('浇花',self.command.call_args.args[1]['title'])

    async def test_two_awaiting_items_require_explicit_selection_during_create(self):
        await respond(self.conn,'明天提醒我浇花')
        draft=self.conn.schedule_pending
        self.snapshot['items']=[dict(id=str(i),title=f'喝水{i}',status='awaiting_confirmation',version=2,dueAt=NOW) for i in (1,2)]
        self.assertIn('哪一条',await respond(self.conn,'收到'))
        self.command.assert_not_awaited()
        self.command.side_effect=lambda device,body:dict(self.snapshot['items'][1],status='completed',version=3)
        with patch('core.device.face.send_face',AsyncMock()): await respond(self.conn,'第二条')
        self.assertEqual('2',self.command.call_args.args[1]['id'])
        self.assertIs(draft,self.conn.schedule_pending)

    async def test_list_and_cancel_another_item_preserve_single_create(self):
        await respond(self.conn,'明天提醒我浇花')
        draft=self.conn.schedule_pending
        item=dict(id='water',title='喝水',status='scheduled',version=2,dueAt=NOW+3600000)
        self.snapshot['items']=[item]
        self.assertIn('喝水',await respond(self.conn,'今天日程'))
        self.command.assert_not_awaited()
        self.command.side_effect=lambda device,body:dict(item,status='cancelled',version=3)
        self.assertIn('不提醒了',await respond(self.conn,'取消喝水提醒'))
        self.assertIs(draft,self.conn.schedule_pending)

    async def test_batch_then_single_routes_field_answer_to_latest_single(self):
        await respond(self.conn,'明天12:00提醒我吃药，后天提醒我去医院')
        batch=self.conn.schedule_batch_pending
        await respond(self.conn,'明天提醒我浇花')
        self.assertIn('浇花',await respond(self.conn,'下午三点'))
        self.assertEqual(1,self.command.await_count)
        self.assertEqual('浇花',self.command.call_args.args[1]['title'])
        self.assertIs(batch,self.conn.schedule_batch_pending)
        await respond(self.conn,'16点')
        self.assertEqual(['浇花','吃药','去医院'],[c.args[1]['title'] for c in self.command.call_args_list])

    async def test_cancel_only_latest_context_and_casual_time_does_not_fill_any(self):
        await respond(self.conn,'明天12:00提醒我吃药，后天提醒我去医院')
        batch=self.conn.schedule_batch_pending
        await respond(self.conn,'明天提醒我浇花')
        self.assertIsNone(await respond(self.conn,'我下午出去玩'))
        self.command.assert_not_awaited()
        await respond(self.conn,'取消设置')
        self.assertIsNone(self.conn.schedule_pending)
        self.assertIs(batch,self.conn.schedule_batch_pending)

    async def test_device_session_and_expiry_invalidate_field_ownership(self):
        for change in ('device','session','expiry'):
            self.conn=NS(headers={'device-id':'device'},session_id='session',logger=Mock())
            await respond(self.conn,'明天提醒我浇花')
            if change=='device': self.conn.headers['device-id']='other'
            elif change=='session': self.conn.session_id='other'
            else: self.conn.schedule_pending['expires']=0
            self.assertIn('已失效',await respond(self.conn,'下午三点'))
        self.command.assert_not_awaited()

    async def test_single_lost_response_recovers_same_request_after_trigger_or_completion(self):
        self.command.side_effect=TimeoutError()
        await respond(self.conn,'明天15:00提醒我浇花')
        original=dict(self.command.call_args.args[1])
        self.snapshot['serverNow']=original['triggerAt']+86400000
        self.command.side_effect=lambda device,body:receipt(device,body,'completed')
        self.assertIn('记为收到了',await respond(self.conn,'重试'))
        self.assertEqual(original,self.command.call_args.args[1])
        self.assertIsNone(self.conn.schedule_pending)

    async def test_partial_batch_recovery_accepts_paused_cycle_and_changed_delivery_time(self):
        async def lose_second(device,body):
            if body['title']=='浇花': raise TimeoutError()
            return receipt(device,body)
        self.command.side_effect=lose_second
        await respond(self.conn,'明天12:00提醒我吃药，每天15:00提醒我浇花')
        original=dict(self.command.call_args.args[1])
        self.command.side_effect=lambda device,body:receipt(device,body,'paused')
        self.assertIn('现在暂停了',await respond(self.conn,'重试'))
        self.assertEqual(original,self.command.call_args.args[1])
        self.assertEqual(3,self.command.await_count)
        self.assertIsNone(self.conn.schedule_batch_pending)

    async def test_hidden_unanswered_items_prevent_blind_ack(self):
        self.snapshot.update(unansweredCount=202,items=[dict(id='one',title='喝水',status='missed',version=2,dueAt=NOW)])
        self.assertIn('没查到',await respond(self.conn,'收到'))
        self.command.assert_not_awaited()

    async def test_expired_draft_does_not_block_explicit_query_and_questions_do_not_ack(self):
        await respond(self.conn,'明天提醒我浇花')
        self.conn.schedule_pending['expires']=0
        item=dict(id='one',title='喝水',status='awaiting_confirmation',version=2,dueAt=NOW)
        self.snapshot['items']=[item]
        self.assertIn('喝水',await respond(self.conn,'今天日程'))
        self.assertIsNone(await respond(self.conn,'我收到快递了吗'))
        self.assertIsNone(await respond(self.conn,'我没有收到提醒'))
        self.command.assert_not_awaited()

    async def test_interrogative_queries_read_state_with_and_without_each_draft(self):
        import copy
        from core.reminders.routing import ATTRS
        queries=('今天有什么提醒？','今天有什么提醒?','明天有哪些日程？','明天有哪些日程?')
        for owner in (None, *ATTRS):
            for query in queries:
                with self.subTest(owner=owner, query=query):
                    self.conn=NS(headers={'device-id':'device'},session_id='session',logger=Mock(),reminder_response_until=float('inf'))
                    draft=None
                    if owner:
                        import time
                        draft=dict(text='明天提醒我浇花',title='浇花',action='create',baseNow=NOW,
                            expires=time.monotonic()+180,session='session',device='device',order=1)
                        if owner=='schedule_batch_pending': draft['batch']=[dict(title='浇花',source=draft['text'])]
                        setattr(self.conn,owner,draft)
                    before=copy.deepcopy(draft)
                    day=ms('2026-10-01T15:00:00') if '明天' in query else NOW+3600000
                    self.snapshot['items']=[dict(id='water',title='喝水',status='scheduled',version=1,dueAt=day)]
                    self.state.reset_mock()
                    with patch('core.reminders.intent.classify',AsyncMock()) as classify:
                        self.assertIn('喝水',await respond(self.conn,query))
                        classify.assert_not_awaited()
                    self.state.assert_awaited_once_with('device')
                    if owner:
                        self.assertIs(draft,getattr(self.conn,owner))
                        self.assertEqual(before,draft)
        self.command.assert_not_awaited()

    async def test_question_negation_and_quotes_do_not_authorize_writes(self):
        await respond(self.conn,'明天提醒我浇花')
        draft=self.conn.schedule_pending
        for text in ('收到？','取消浇花提醒?','改到明天15:00？','如果今天有什么提醒？','他说“今天有什么提醒？”','别设置明天15:00提醒我浇花'):
            self.assertIsNone(await respond(self.conn,text))
            self.assertIs(draft,self.conn.schedule_pending)
        self.command.assert_not_awaited()

    async def test_unpunctuated_status_questions_and_negations_preserve_saved_reminders(self):
        import copy
        await respond(self.conn,'明天提醒我拿快递')
        draft=self.conn.schedule_pending
        self.snapshot['items']=[dict(id='flower',title='浇花',status='scheduled',version=2,dueAt=NOW+3600000)]
        self.snapshot['plans']=[dict(id='cycle',title='浇花',status='active',version=2,
            recurrence='daily',initialAt=NOW,nextAt=NOW+86400000)]
        before=copy.deepcopy(self.snapshot)
        for text in ('浇花的提醒取消了吗','浇花周期提醒暂停了吗','浇花提醒是不是取消了',
                     '浇花提醒有没有恢复','如何取消浇花提醒','浇花提醒怎么取消',
                     '浇花提醒取消了没有','浇花提醒还没取消',
                     '不要取消浇花提醒','别暂停浇花周期提醒','不要把浇花提醒改到明天三点',
                     '明天三点提醒我浇花吗'):
            with self.subTest(text=text):
                self.assertIsNone(await respond(self.conn,text))
                self.assertIs(draft,self.conn.schedule_pending)
                self.assertEqual(before,self.snapshot)
        self.command.assert_not_awaited()

    async def test_explicit_cancel_still_updates_only_the_named_reminder(self):
        flower=dict(id='flower',title='浇花',status='scheduled',version=2,dueAt=NOW+3600000)
        self.snapshot['items']=[flower,dict(flower,id='water',title='喝水')]
        self.command.side_effect=lambda device,body:dict(flower,status='cancelled',version=3)
        self.assertIn('不提醒了',await respond(self.conn,'请取消浇花提醒'))
        self.command.assert_awaited_once_with('device',dict(action='cancel',id='flower',version=2))

    async def test_single_definite_rejection_accepts_new_authorized_time(self):
        self.command.side_effect=OperationRejected(400,'时间已过去')
        self.assertIn('没有存好',await respond(self.conn,'10分钟后提醒我浇花'))
        original=dict(self.command.call_args.args[1])
        self.assertNotIn('body',self.conn.schedule_pending)
        self.snapshot['serverNow']=NOW+600000
        self.command.side_effect=receipt
        self.assertIn('提醒你',await respond(self.conn,'20分钟后'))
        changed=self.command.call_args.args[1]
        self.assertNotEqual(original['requestId'],changed['requestId'])
        self.assertEqual(NOW+1800000,changed['triggerAt'])
        self.assertEqual('浇花',changed['title'])

    async def test_single_rejected_clock_needs_date_and_can_cancel(self):
        self.command.side_effect=OperationRejected(400,'容量限制')
        await respond(self.conn,'明天15:00提醒我浇花')
        self.assertIn('哪天几点',await respond(self.conn,'下午四点'))
        self.assertEqual(1,self.command.await_count)
        self.command.side_effect=receipt
        self.assertIn('提醒你',await respond(self.conn,'后天'))
        self.assertEqual(ms('2026-10-02T16:00:00'),self.command.call_args.args[1]['triggerAt'])
        self.command.side_effect=OperationRejected(403,'设备不可用')
        await respond(self.conn,'明天15:00提醒我喝水')
        self.assertIn('取消',await respond(self.conn,'取消设置'))
        self.assertIsNone(self.conn.schedule_pending)

    async def test_single_definite_rejection_explicit_retry_uses_same_body(self):
        self.command.side_effect=OperationRejected(400,'容量限制')
        await respond(self.conn,'明天15:00提醒我浇花')
        original=dict(self.command.call_args.args[1])
        self.command.side_effect=receipt
        await respond(self.conn,'重试')
        self.assertEqual(original,self.command.call_args.args[1])

    async def test_refused_creation_explicit_clock_correction_does_not_edit_other_items(self):
        self.snapshot['items']=[dict(id='water',title='喝水',status='scheduled',version=1,dueAt=NOW+3600000)]
        self.command.side_effect=OperationRejected(400,'容量限制')
        await respond(self.conn,'明天15:00提醒我浇花')
        self.command.side_effect=receipt
        await respond(self.conn,'改到后天16:00')
        body=self.command.call_args.args[1]
        self.assertEqual('create',body['action'])
        self.assertEqual('浇花',body['title'])
        self.assertEqual(ms('2026-10-02T16:00:00'),body['triggerAt'])

    async def test_refused_batch_explicit_clock_correction_keeps_saved_entry(self):
        async def reject_second(device,body):
            if body['title']=='浇花': raise OperationRejected(400,'容量限制')
            return receipt(device,body)
        self.command.side_effect=reject_second
        await respond(self.conn,'明天12:00提醒我吃药，明天15:00提醒我浇花')
        self.command.side_effect=receipt
        self.assertIn('这两条提醒都记好了',await respond(self.conn,'改到后天16:00'))
        self.assertEqual(3,self.command.await_count)
        self.assertEqual('create',self.command.call_args.args[1]['action'])

    async def test_single_conflict_or_rejection_after_timeout_keeps_original(self):
        for first in (OperationRejected(409,'编号冲突'),TimeoutError()):
            with self.subTest(first=type(first).__name__):
                self.conn=NS(headers={'device-id':'device'},session_id='session',logger=Mock())
                self.command.side_effect=first
                await respond(self.conn,'明天15:00提醒我浇花')
                original=dict(self.command.call_args.args[1])
                self.command.side_effect=OperationRejected(400,'用户配置变化')
                self.assertIn('有没有存好，我还没确认',await respond(self.conn,'重试'))
                count=self.command.await_count
                self.assertIn('我还没确认',await respond(self.conn,'后天16:00'))
                self.assertEqual(count,self.command.await_count)
                self.assertEqual(original,self.conn.schedule_pending['body'])

    async def test_partial_batch_definite_rejection_corrects_only_unsaved_entry(self):
        async def reject_second(device,body):
            if body['title']=='浇花': raise OperationRejected(400,'容量限制')
            return receipt(device,body)
        self.command.side_effect=reject_second
        answer=await respond(self.conn,'明天12:00提醒我吃药，每天15:00提醒我浇花，后天16:00提醒我去医院')
        self.assertIn('确认存好',answer)
        self.assertIn('没有存好',answer)
        pending=self.conn.schedule_batch_pending
        saved=pending['batch'][0]['receipt']
        rejected=dict(self.command.call_args.args[1])
        later=dict(pending['batch'][2]['body'])
        self.assertFalse(pending['retry'])
        self.assertIn('哪天几点',await respond(self.conn,'下午四点'))
        self.assertEqual(2,self.command.await_count)
        self.command.side_effect=receipt
        answer=await respond(self.conn,'后天')
        self.assertIn('这三条提醒都记好了',answer)
        self.assertIs(saved,pending['batch'][0]['receipt'])
        calls=[c.args[1] for c in self.command.call_args_list]
        self.assertEqual(['吃药','浇花','浇花','去医院'],[c['title'] for c in calls])
        self.assertNotEqual(rejected['requestId'],calls[2]['requestId'])
        self.assertEqual(ms('2026-10-02T16:00:00'),calls[2]['triggerAt'])
        self.assertEqual(later,calls[3])

    async def test_batch_definite_rejection_retry_or_cancel_retains_successes(self):
        async def reject_second(device,body):
            if body['title']=='浇花': raise OperationRejected(400,'容量限制')
            return receipt(device,body)
        self.command.side_effect=reject_second
        await respond(self.conn,'明天12:00提醒我吃药，明天15:00提醒我浇花')
        original=dict(self.command.call_args.args[1])
        await respond(self.conn,'重试')
        self.assertEqual(original,self.command.call_args.args[1])
        answer=await respond(self.conn,'取消设置')
        self.assertIn('存好的这些还在',answer)
        self.assertIn('吃药',answer)
        self.assertNotIn('仍需',answer)
        self.assertIsNone(self.conn.schedule_batch_pending)

    async def test_batch_unknown_recovers_before_expired_unsubmitted_entry(self):
        self.command.side_effect=TimeoutError()
        await respond(self.conn,'明天12:00提醒我吃药，明天15:00提醒我浇花')
        original=dict(self.command.call_args.args[1])
        self.snapshot['serverNow']=ms('2026-10-01T17:00:00')
        self.command.side_effect=lambda device,body:receipt(device,body,'completed')
        answer=await respond(self.conn,'重试')
        self.assertIn('时间已经过去',answer)
        self.assertEqual(original,self.command.call_args.args[1])
        self.assertEqual(2,self.command.await_count)
        self.assertFalse(self.conn.schedule_batch_pending['retry'])
        self.command.side_effect=receipt
        self.assertIn('这几条提醒我都找到了',await respond(self.conn,'明天16:00'))

    async def test_batch_conflict_and_later_refusal_never_unlock_new_identity(self):
        self.command.side_effect=OperationRejected(409,'冲突')
        answer=await respond(self.conn,'明天12:00提醒我吃药，明天15:00提醒我浇花')
        self.assertIn('可能已经存过了',answer)
        original=dict(self.command.call_args.args[1])
        self.snapshot['serverNow']=ms('2026-10-01T17:00:00')
        self.command.side_effect=OperationRejected(400,'配置变化')
        self.assertIn('有没有存好，我还没确认',await respond(self.conn,'重试'))
        self.assertEqual(original,self.command.call_args.args[1])
        count=self.command.await_count
        self.assertIn('没确认',await respond(self.conn,'明天16:00'))
        self.assertEqual(count,self.command.await_count)
        self.assertEqual(original,self.conn.schedule_batch_pending['batch'][0]['body'])

    async def test_cancelled_submission_is_unknown_even_if_retry_is_rejected(self):
        import asyncio
        for source,attr in [('明天15:00提醒我浇花','schedule_pending'),
                ('明天12:00提醒我吃药，明天15:00提醒我浇花','schedule_batch_pending')]:
            with self.subTest(attr=attr):
                self.conn=NS(headers={'device-id':'device'},session_id='session',logger=Mock())
                self.command.side_effect=asyncio.CancelledError()
                with self.assertRaises(asyncio.CancelledError): await respond(self.conn,source)
                draft=getattr(self.conn,attr)
                attempt=draft['batch'][0] if attr=='schedule_batch_pending' else draft
                original=dict(attempt['body'])
                self.assertTrue(attempt['uncertain'])
                self.command.side_effect=OperationRejected(400,'配置变化')
                self.assertIn('有没有存好，我还没确认',await respond(self.conn,'重试'))
                self.assertEqual(original,self.command.call_args.args[1])


class ReceiptIdentityTests(unittest.TestCase):
    def test_mutable_status_and_due_time_do_not_change_original_creation_identity(self):
        body=dict(requestId='id',title='浇花',triggerAt=NOW,recurrence='once')
        for status in ('dispatching','retry_pending','completed','history_removed'):
            self.assertTrue(verified(receipt('device',body,status),body,'device'))
        for key,value in [('requestId','other'),('title','other'),('deviceId','other'),('triggerAt',NOW+1),('recurrence','daily')]:
            result=receipt('device',body); result['creation'][key]=value
            self.assertFalse(verified(result,body,'device'))
