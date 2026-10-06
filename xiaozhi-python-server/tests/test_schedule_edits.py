"""Regression cases for real reminder edits; no LLM or device is required."""
import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.reminders.edits import edit_request, remember, respond_edit
from core.reminders.parser import ZONE
from core.reminders.conversation import respond, relevant
from core.reminders.client import OperationRejected


def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=ZONE).timestamp()*1000)


NOW = ms('2026-09-30T13:11:19')
TOMORROW = ms('2026-10-01T12:00:00')


class ReminderEditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = NS(headers={'device-id': 'device'}, session_id='session', logger=Mock())
        self.item = dict(id='item', title='拿快递', status='scheduled', version=4,
                         dueAt=TOMORROW, originalDueAt=TOMORROW)
        self.plan = dict(id='plan', title='拿快递', status='active', version=3,
                         initialAt=TOMORROW, nextAt=TOMORROW, recurrence='daily')
        self.snapshot = dict(items=[self.item], plans=[], serverNow=NOW)
        self.state = AsyncMock(return_value=self.snapshot)
        self.command = AsyncMock(side_effect=self.receipt)
        for name, mock in [('state', self.state), ('command', self.command)]:
            p = patch('core.reminders.client.'+name, mock)
            p.start()
            self.addCleanup(p.stop)

    def receipt(self, device, body):
        if body['action'] == 'plan_edit':
            self.plan.update(initialAt=body['triggerAt'], nextAt=body['triggerAt'],
                             version=body['version']+1, recurrence=body['recurrence'])
            return dict(id=body['id'])
        item = dict(self.item, id=body['id'] if body['action'] == 'edit' else 'saved',
                    title=body['title'], dueAt=body['triggerAt'], version=body['version']+1)
        if body['action'] == 'plan_to_once':
            return dict(item=item, convertedFrom=body['id'])
        if body['action'] == 'plan_occurrence_edit':
            return dict(item=item, sourcePlan=body['id'])
        return item

    def body(self):
        return self.command.call_args.args[1]

    async def test_sample_relative_shift_uses_existing_time_not_current_time(self):
        self.assertTrue(relevant('明天拿快递的时间延后2小时'))
        answer = await respond(self.conn, '明天拿快递的时间延后2小时')
        self.assertIn('改到', answer)
        self.assertEqual(TOMORROW+7200000, self.body()['triggerAt'])
        self.assertEqual(('item', 4, 'edit'), (self.body()['id'], self.body()['version'], self.body()['action']))

    async def test_change_date_selects_original_record(self):
        await respond_edit(self.conn, '拿快递提醒改到后天下午三点')
        self.assertEqual(ms('2026-10-02T15:00:00'), self.body()['triggerAt'])

    async def test_clock_only_preserves_original_date(self):
        await respond_edit(self.conn, '拿快递提醒改到14:30')
        self.assertEqual(ms('2026-10-01T14:30:00'), self.body()['triggerAt'])

    async def test_old_clock_in_target_description_does_not_confuse_new_clock(self):
        await respond_edit(self.conn, '明天中午12点拿快递的提醒改到14点')
        self.assertEqual(ms('2026-10-01T14:00:00'), self.body()['triggerAt'])

    async def test_shift_crosses_midnight(self):
        self.item['dueAt'] = ms('2026-10-01T23:45:00')
        await respond_edit(self.conn, '明天拿快递往后推半小时')
        self.assertEqual(ms('2026-10-02T00:15:00'), self.body()['triggerAt'])

    async def test_ambiguous_clock_then_14_point_replaces_question_clock(self):
        self.assertIn('上午还是下午', await respond_edit(self.conn, '明天拿快递改到三点'))
        self.command.assert_not_awaited()
        self.assertIn('改到', await respond_edit(self.conn, '14点'))
        self.assertEqual(ms('2026-10-01T14:00:00'), self.body()['triggerAt'])
        self.assertIsNone(self.conn.schedule_edit_pending)

    async def test_period_only_followup_completes_clock(self):
        await respond_edit(self.conn, '明天拿快递改到三点')
        await respond_edit(self.conn, '下午')
        self.assertEqual(ms('2026-10-01T15:00:00'), self.body()['triggerAt'])

    async def test_unrelated_time_mention_does_not_answer_pending_question(self):
        await respond_edit(self.conn, '明天拿快递改到三点')
        self.assertIsNone(await respond_edit(self.conn, '我下午要出去玩'))
        self.command.assert_not_awaited()
        self.assertIsNotNone(self.conn.schedule_edit_pending)

    async def test_daily_creation_then_correction_converts_with_one_command(self):
        self.snapshot.update(items=[], plans=[])
        async def create(device, body):
            self.plan['id'] = body['requestId']
            self.snapshot['plans'] = [self.plan]
            return dict(id=body['requestId'],creation=dict(requestId=body['requestId'],deviceId=device,title=body['title'],triggerAt=body['triggerAt'],recurrence=body['recurrence'],seconds=None),current=dict(self.plan))
        self.command.side_effect = create
        self.assertIn('提醒你', await respond(self.conn, '每天中午12点提醒我拿快递'))
        self.command.reset_mock()
        self.command.side_effect = self.receipt
        self.assertIn('原来的周期取消了', await respond(self.conn, '说错了，是明天中午12点提醒我'))
        self.command.assert_awaited_once()
        self.assertEqual('plan_to_once', self.body()['action'])
        self.assertEqual(TOMORROW, self.body()['triggerAt'])

    async def test_specific_occurrence_does_not_change_future_plan(self):
        self.snapshot.update(items=[], plans=[self.plan])
        await respond_edit(self.conn, '明天拿快递的时间延后2小时')
        self.assertEqual('plan_occurrence_edit', self.body()['action'])
        self.assertEqual(TOMORROW, self.body()['occurrenceAt'])
        self.assertEqual(TOMORROW+7200000, self.body()['triggerAt'])
        self.assertEqual(TOMORROW, self.plan['initialAt'])

    async def test_not_daily_correction_converts_to_once(self):
        self.snapshot.update(items=[], plans=[self.plan])
        remember(self.conn, 'plan', self.plan)
        self.assertIn('原来的周期取消了', await respond_edit(self.conn, '不是每天，是明天中午12点提醒我'))
        self.assertEqual('plan_to_once', self.body()['action'])

    async def test_specific_occurrence_can_move_to_another_day(self):
        self.snapshot.update(items=[], plans=[self.plan])
        await respond_edit(self.conn, '明天拿快递改到后天下午三点')
        self.assertEqual(TOMORROW, self.body()['occurrenceAt'])
        self.assertEqual(ms('2026-10-02T15:00:00'), self.body()['triggerAt'])

    async def test_plan_scope_is_asked_and_whole_edit_is_verified(self):
        self.snapshot.update(items=[], plans=[self.plan])
        self.assertIn('以后都改', await respond_edit(self.conn, '拿快递提醒改到14点'))
        self.command.assert_not_awaited()
        self.assertIn('以后的周期按新时间来', await respond_edit(self.conn, '整个周期'))
        self.assertEqual('plan_edit', self.body()['action'])
        self.assertEqual('daily', self.body()['recurrence'])
        self.assertEqual(ms('2026-10-01T14:00:00'), self.body()['triggerAt'])

    async def test_duplicates_require_bound_selection(self):
        second = dict(self.item, id='other')
        self.snapshot['items'].append(second)
        self.assertIn('哪一条', await respond_edit(self.conn, '明天拿快递延后两小时'))
        self.command.assert_not_awaited()
        await respond_edit(self.conn, '第二条')
        self.assertEqual('other', self.body()['id'])

    async def test_version_change_during_question_does_not_mutate(self):
        await respond_edit(self.conn, '明天拿快递改到三点')
        self.item['version'] += 1
        self.assertIn('已经变了', await respond_edit(self.conn, '14点'))
        self.command.assert_not_awaited()

    async def test_new_named_edit_does_not_overwrite_old_pending_target(self):
        self.snapshot['items'].append(dict(self.item, id='water', title='喝水'))
        await respond_edit(self.conn, '明天拿快递改到三点')
        await respond_edit(self.conn, '喝水提醒改到14点')
        self.assertEqual('water', self.body()['id'])

    async def test_timeout_retry_verifies_saved_record_without_second_shift(self):
        self.command.side_effect = TimeoutError()
        self.assertIn('我还没确认', await respond_edit(self.conn, '明天拿快递延后两小时'))
        requested = dict(self.body())
        self.item.update(dueAt=requested['triggerAt'], version=requested['version']+1)
        self.assertIn('改到', await respond_edit(self.conn, '重试'))
        self.command.assert_awaited_once()

    async def test_timeout_retry_reuses_exact_request(self):
        self.command.side_effect = TimeoutError()
        await respond_edit(self.conn, '明天拿快递延后两小时')
        requested = dict(self.body())
        self.command.side_effect = self.receipt
        await respond_edit(self.conn, '重试')
        self.assertEqual(requested, self.body())

    async def test_unverified_retry_does_not_accept_another_clock(self):
        self.command.side_effect = TimeoutError()
        await respond_edit(self.conn, '明天拿快递延后两小时')
        self.assertIn('我还没确认', await respond_edit(self.conn, '14点'))
        self.command.assert_awaited_once()

    async def test_wrong_receipt_and_conflict_never_claim_success(self):
        self.command.side_effect = None
        self.command.return_value = dict(self.item)
        self.assertIn('我还没确认', await respond_edit(self.conn, '明天拿快递延后两小时'))
        self.command.side_effect = OperationRejected(409, 'changed')
        self.assertIn('修改没被接受', await respond_edit(self.conn, '重试'))
        self.assertIsNone(self.conn.schedule_edit_pending)

    async def test_unbound_and_wrong_device_corrections_cannot_select_random_record(self):
        remember(self.conn, 'item', self.item)
        self.conn.headers['device-id'] = 'other-device'
        self.assertIn('没有找到', await respond_edit(self.conn, '说错了，是明天14点提醒我'))
        self.command.assert_not_awaited()

    async def test_unknown_title_cannot_modify_only_existing_item(self):
        self.assertIn('没有找到', await respond_edit(self.conn, '吃饭提醒改到明天14点'))
        self.command.assert_not_awaited()

    async def test_negation_hypothetical_and_quotation_do_not_write(self):
        for text in ('不要把拿快递延后两小时', '别修改拿快递提醒到14点',
                     '如果把拿快递改到明天14点', '他说“拿快递延后两小时”'):
            with self.subTest(text=text):
                self.assertFalse(edit_request(text))
                self.assertIsNone(await respond_edit(self.conn, text))
        self.command.assert_not_awaited()

    async def test_new_clock_can_replace_invalid_relative_time(self):
        await respond_edit(self.conn, '拿快递提前两百小时')
        self.command.assert_not_awaited()
        self.assertIn('改到', await respond_edit(self.conn, '14点'))
        self.assertEqual(ms('2026-10-01T14:00:00'), self.body()['triggerAt'])

