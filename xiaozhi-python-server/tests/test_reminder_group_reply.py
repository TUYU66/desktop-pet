"""Source-only regression: plural acknowledgement, context, partial receipts and drafts."""
import json
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from core.reminders.reply import remember, recent, all_ack
from core.reminders.intent import classify
from core.reminders.conversation import respond


class GroupReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn=NS(headers={'device-id':'target'},session_id='session',logger=Mock())
        self.items=[dict(id='parcel',title='拿快递',status='retry_pending',version=4),
                    dict(id='water',title='喝水',status='expired',version=8)]
        self.snapshot=dict(items=self.items,plans=[],serverNow=1000,unansweredCount=2)
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('core.reminders.client.state',new=AsyncMock(return_value=self.snapshot)))
        def receipt(device,body):
            self.assertEqual('target',device)
            return dict(id=body['id'],status='completed',version=body['version']+1)
        self.commands=self.stack.enter_context(patch('core.reminders.client.command',new=AsyncMock(side_effect=receipt)))
        self.face=self.stack.enter_context(patch('core.device.face.send_face',new=AsyncMock()))

    async def test_common_plural_acknowledgements_confirm_both_without_model(self):
        for text in ('两条都收到了','这两个提醒我都收到了','全部收到','都知道了','两条都确认了'):
            with self.subTest(text=text):
                self.commands.reset_mock()
                remember(self.conn,self.items)
                answer=await respond(self.conn,text)
                self.assertIn('这两条提醒都记为收到了',answer)
                self.assertEqual({'parcel','water'},{c.args[1]['id'] for c in self.commands.await_args_list})
                self.assertEqual({4,8},{c.args[1]['version'] for c in self.commands.await_args_list})

    async def test_recent_two_do_not_include_a_new_unmentioned_third_reminder(self):
        remember(self.conn,self.items)
        self.items.append(dict(id='other',title='吃饭',status='awaiting_confirmation',version=2))
        self.snapshot['unansweredCount']=3
        await respond(self.conn,'两条都收到了')
        self.assertEqual({'parcel','water'},{c.args[1]['id'] for c in self.commands.await_args_list})

    async def test_all_without_recent_context_uses_current_complete_unanswered_list(self):
        self.items.append(dict(id='future',title='睡觉',status='scheduled',version=1))
        await respond(self.conn,'全部收到')
        self.assertEqual({'parcel','water'},{c.args[1]['id'] for c in self.commands.await_args_list})

    async def test_count_mismatch_or_truncated_group_never_silently_confirms_a_subset(self):
        self.assertIn('对应上',await respond(self.conn,'三条都收到了'))
        self.commands.assert_not_awaited()
        remember(self.conn,self.items,total=3)
        self.assertIn('对应上',await respond(self.conn,'全部收到'))
        self.commands.assert_not_awaited()
        self.conn.reminder_reply_context=None; self.snapshot['unansweredCount']=3
        self.assertIn('对应上',await respond(self.conn,'全部收到'))
        self.commands.assert_not_awaited()

    async def test_prior_selection_question_is_consumed_by_plural_answer(self):
        self.assertIn('哪一条',await respond(self.conn,'收到'))
        self.assertIsNotNone(self.conn.schedule_control_pending)
        self.commands.assert_not_awaited()
        self.assertIn('都记为收到了',await respond(self.conn,'两条都收到了'))
        self.assertIsNone(self.conn.schedule_control_pending)

    async def test_freeform_plural_reply_also_resolves_an_existing_choice_question(self):
        await respond(self.conn,'收到')
        self.conn.llm=Mock()
        self.conn.llm.response_json.return_value=dict(action='confirm',targetId=None,targetIds=['parcel','water'])
        answer=await respond(self.conn,'刚才那两条我都看到了，谢谢')
        self.assertIn('都记为收到了',answer)
        self.assertEqual(2,self.commands.await_count)
        self.assertIsNone(self.conn.schedule_control_pending)

    async def test_plural_answer_preserves_unrelated_create_draft(self):
        draft=dict(action='create',text='明天提醒我浇花',title='浇花',baseNow=1000,
                   session='session',device='target',expires=time.monotonic()+180,order=1)
        self.conn.schedule_pending=draft
        remember(self.conn,self.items)
        await respond(self.conn,'两条都收到了')
        self.assertIs(draft,self.conn.schedule_pending)
        self.assertEqual(2,self.commands.await_count)

    async def test_failed_member_is_reported_separately_without_false_all_success(self):
        def partial(device,body):
            if body['id']=='water': raise TimeoutError()
            return dict(id=body['id'],status='completed',version=body['version']+1)
        self.commands.side_effect=partial
        answer=await respond(self.conn,'两条都收到了')
        self.assertIn('“拿快递”已经记为收到了',answer)
        self.assertIn('“喝水”有没有记为收到',answer)
        self.assertNotIn('都记为收到了',answer)

    async def test_mismatched_receipt_is_not_successful_confirmation(self):
        self.commands.side_effect=lambda *_:dict(id='foreign',status='completed',version=20)
        answer=await respond(self.conn,'两条都收到了')
        self.assertIn('我还没确认',answer)
        self.assertNotIn('都记为收到了',answer)
        self.face.assert_not_awaited()

    async def test_single_recent_target_resolves_received_without_touching_other_reminders(self):
        remember(self.conn,[self.items[1]])
        await respond(self.conn,'收到')
        self.commands.assert_awaited_once_with('target',dict(action='confirm',id='water',version=8))

    async def test_freeform_model_reply_can_return_multiple_validated_targets(self):
        self.conn.llm=Mock()
        self.conn.llm.response_json.return_value=json.dumps(dict(action='confirm',targetId=None,targetIds=['parcel','water']))
        remember(self.conn,self.items)
        await respond(self.conn,'拿快递和喝水的提醒我都看到了，谢啦')
        self.assertEqual(2,self.commands.await_count)
        data=json.loads(self.conn.llm.response_json.call_args.args[1])
        self.assertEqual(['parcel','water'],[x['id'] for x in data['recentReminders']])

    async def test_model_cannot_return_unknown_duplicate_or_conflicting_ids(self):
        self.conn.llm=Mock()
        for ids,target,action in [(['parcel','foreign'],None,'confirm'),(['parcel','parcel'],None,'confirm'),
                                  (['parcel','water'],'parcel','confirm'),(['parcel','water'],None,'snooze')]:
            self.conn.llm.response_json.return_value=dict(action=action,targetId=target,targetIds=ids)
            with self.assertRaises(ValueError): await classify(self.conn,'都看到了',self.items)
        self.commands.assert_not_awaited()

    async def test_model_all_must_cover_recent_group_not_a_subset_or_new_item(self):
        self.conn.llm=Mock()
        remember(self.conn,self.items)
        self.items.append(dict(id='new',title='吃饭',status='awaiting_confirmation',version=2))
        for ids in (['parcel'],['parcel','water','new']):
            self.conn.llm.response_json.return_value=dict(action='confirm',targetId=None,targetIds=ids)
            self.assertEqual('ambiguous',(await classify(self.conn,'刚才那两条我都看到了，谢谢',self.items))['action'])
        self.conn.llm.response_json.return_value=dict(action='confirm',targetId=None,targetIds=['parcel','water'])
        self.assertEqual('confirm',(await classify(self.conn,'刚才那两条我都看到了，谢谢',self.items))['action'])

    def test_recent_context_is_bound_to_device_session_and_expiry(self):
        for change in ('device','session','expiry'):
            self.conn.headers={'device-id':'target'}; self.conn.session_id='session'
            remember(self.conn,self.items)
            if change=='device': self.conn.headers={'device-id':'other'}
            if change=='session': self.conn.session_id='new-session'
            if change=='expiry': self.conn.reminder_reply_context['expires']=0
            self.assertIsNone(recent(self.conn))

    async def test_questions_negation_quotes_and_mixed_snooze_do_not_use_bulk_confirm(self):
        self.conn.llm=Mock()
        self.conn.llm.response_json.return_value=dict(action='none',targetId=None)
        for text in ('两条都没收到','两条都收到了吗','如果两条都收到了','他说“两条都收到了”',
                     '两条都收到是什么意思','两条都收到了，十分钟后再提醒'):
            self.assertIsNone(all_ack(text))
            await respond(self.conn,text)
        self.commands.assert_not_awaited()


if __name__=='__main__': unittest.main()
