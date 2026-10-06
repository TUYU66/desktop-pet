"""Multi-reminder creation and month-boundary regressions; test source only."""
import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.reminders.batch import handle, split_requests
from core.reminders.conversation import respond
from core.reminders.parser import parse_time, title_from, ZONE


def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=ZONE).timestamp()*1000)


NOW = ms('2026-09-30T13:57:38')
REQUEST = '明天中午12点提醒我吃药，3号下午16点提醒我去医院'


class CalendarDayTests(unittest.TestCase):
    def test_bare_day_crosses_month_and_year(self):
        self.assertEqual(ms('2026-10-03T16:00:00'), parse_time('3号下午16点', NOW)[0])
        self.assertEqual(ms('2027-01-02T16:00:00'), parse_time('2号下午16点', ms('2026-12-31T13:00:00'))[0])

    def test_explicit_month_is_not_silently_rolled(self):
        self.assertIsNone(parse_time('本月3号下午16点', NOW)[0])
        self.assertIsNone(parse_time('9月3号下午16点', NOW)[0])
        self.assertIsNone(parse_time('31号下午16点', NOW)[0])
        self.assertIsNone(parse_time('2027年3号下午16点', NOW)[0])

    def test_same_day_past_clock_is_not_moved_to_next_month(self):
        self.assertIsNone(parse_time('30号上午8点', NOW)[0])
        self.assertEqual(ms('2026-10-03T16:00:00'), parse_time('下个月3号下午16点', NOW)[0])

    def test_day_at_start_of_title_is_removed(self):
        self.assertEqual('去医院', title_from('提醒我3号下午16点去医院'))

    def test_split_ignores_quoted_markers_and_weekday_commas(self):
        quoted = '明天16点提醒我看《3号下午16点提醒我去医院》'
        self.assertIsNone(split_requests(quoted))
        self.assertEqual(2, len(split_requests('明天16点提醒我吃药，每周一，三，五下午16点提醒我喝水')))
        self.assertEqual(2, len(split_requests('明天16点提醒我吃药然后3号16点提醒我去医院')))


class BatchCreationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = NS(headers={'device-id': 'device'}, session_id='session', logger=Mock())
        self.snapshot = dict(items=[], plans=[], serverNow=NOW)
        self.state = AsyncMock(return_value=self.snapshot)
        self.commands = AsyncMock(side_effect=self.receipt)
        for name, mock in [('state', self.state), ('command', self.commands)]:
            p = patch('core.reminders.client.'+name, mock)
            p.start()
            self.addCleanup(p.stop)

    def receipt(self, device, body):
        return dict(id=body['requestId'], title=body['title'], deviceId=device,
            creation=dict(requestId=body['requestId'],deviceId=device,title=body['title'],triggerAt=body['triggerAt'],recurrence=body['recurrence'],seconds=None),
            current=dict(id=body['requestId'],title=body['title'],status='scheduled' if body['recurrence']=='once' else 'active',dueAt=body['triggerAt']))

    def bodies(self):
        return [call.args[1] for call in self.commands.call_args_list]

    async def test_screenshot_creates_two_real_records_and_confirms_each(self):
        answer = await respond(self.conn, REQUEST)
        self.assertIn('这两条提醒都记好了', answer)
        self.assertIn('明天中午12点', answer)
        self.assertIn('10月3日下午4点', answer)
        self.assertIn('提醒你吃药', answer)
        self.assertIn('提醒你去医院', answer)
        first, second = self.bodies()
        self.assertEqual(('吃药', ms('2026-10-01T12:00:00')), (first['title'], first['triggerAt']))
        self.assertEqual(('去医院', ms('2026-10-03T16:00:00')), (second['title'], second['triggerAt']))
        self.assertNotEqual(first['requestId'], second['requestId'])
        self.assertTrue(self.conn.schedule_operation_complete)
        self.assertIsNone(self.conn.schedule_batch_pending)

    async def test_second_marker_can_precede_its_time(self):
        await respond(self.conn, '明天中午12点提醒我吃药，提醒我3号下午16点去医院')
        self.assertEqual('去医院', self.bodies()[1]['title'])
        self.assertEqual(ms('2026-10-03T16:00:00'), self.bodies()[1]['triggerAt'])

    async def test_missing_clock_is_asked_before_any_write(self):
        answer = await respond(self.conn, '明天中午12点提醒我吃药，3号提醒我去医院')
        self.assertIn('第2条', answer)
        self.assertIn('几点', answer)
        self.commands.assert_not_awaited()
        answer = await respond(self.conn, '16点')
        self.assertIn('这两条提醒都记好了', answer)
        self.assertEqual(ms('2026-10-03T16:00:00'), self.bodies()[1]['triggerAt'])

    async def test_ambiguous_clock_can_be_replaced_by_concrete_reply(self):
        await respond(self.conn, '明天中午12点提醒我吃药，3号三点提醒我去医院')
        self.commands.assert_not_awaited()
        await respond(self.conn, '16点')
        self.assertEqual(ms('2026-10-03T16:00:00'), self.bodies()[1]['triggerAt'])

    async def test_missing_clock_then_clock_then_period_keeps_both_answers(self):
        await respond(self.conn, '明天12:00提醒我吃药，3号提醒我去医院')
        self.assertIn('上午还是下午', await respond(self.conn, '三点'))
        self.commands.assert_not_awaited()
        self.assertIn('这两条提醒都记好了', await respond(self.conn, '下午'))
        self.assertEqual(ms('2026-10-03T15:00:00'), self.bodies()[1]['triggerAt'])

    async def test_date_then_clock_then_period_keeps_all_fields(self):
        await respond(self.conn, '明天12:00提醒我吃药，32号提醒我去医院')
        await respond(self.conn, '后天')
        await respond(self.conn, '三点')
        self.commands.assert_not_awaited()
        await respond(self.conn, '下午')
        self.assertEqual(ms('2026-10-02T15:00:00'), self.bodies()[1]['triggerAt'])

    async def test_acknowledgement_during_batch_question_completes_real_reminder(self):
        await respond(self.conn, '明天12:00提醒我吃药，3号提醒我去医院')
        batch = self.conn.schedule_batch_pending
        item = dict(id='waiting', title='喝水', status='awaiting_confirmation', version=2, dueAt=NOW)
        self.snapshot['items'] = [item]
        self.commands.side_effect = lambda device, body: dict(item, status='completed', version=3)
        with patch('core.device.face.send_face', AsyncMock()):
            self.assertIn('记为收到了', await respond(self.conn, '收到'))
        self.assertEqual(dict(action='confirm', id='waiting', version=2), self.bodies()[0])
        self.assertIs(batch, self.conn.schedule_batch_pending)

    async def test_query_pause_and_cancel_during_batch_question_keep_batch(self):
        await respond(self.conn, '明天12:00提醒我吃药，3号提醒我去医院')
        batch = self.conn.schedule_batch_pending
        item = dict(id='other', title='喝水', status='scheduled', version=2, dueAt=NOW+3600000)
        plan = dict(id='plan', title='散步', status='active', version=3, initialAt=NOW+3600000,
                    nextAt=NOW+3600000, recurrence='daily')
        self.snapshot.update(items=[item], plans=[plan])
        self.assertIn('喝水', await respond(self.conn, '今天日程'))
        self.commands.assert_not_awaited()
        self.commands.side_effect = lambda device, body: dict(id=body['id']) if body['action'].startswith('plan_') else dict(item, status='cancelled', version=3)
        self.assertIn('后面的提醒先暂停', await respond(self.conn, '暂停散步周期'))
        self.assertIn('不提醒了', await respond(self.conn, '取消喝水提醒'))
        self.assertEqual(['plan_pause', 'cancel'], [x['action'] for x in self.bodies()])
        self.assertIs(batch, self.conn.schedule_batch_pending)

    async def test_new_edit_question_takes_priority_over_existing_batch_question(self):
        await respond(self.conn, '明天12:00提醒我吃药，3号提醒我去医院')
        batch = self.conn.schedule_batch_pending
        item = dict(id='other', title='喝水', status='scheduled', version=2, dueAt=NOW+3600000)
        self.snapshot['items'] = [item]
        self.assertIn('上午还是下午', await respond(self.conn, '喝水提醒改到明天三点'))
        self.commands.side_effect = lambda device, body: dict(item, dueAt=body['triggerAt'], status='scheduled', version=3)
        self.assertIn('改到', await respond(self.conn, '下午'))
        self.assertEqual(('edit', 'other', ms('2026-10-01T15:00:00')), (self.bodies()[0]['action'], self.bodies()[0]['id'], self.bodies()[0]['triggerAt']))
        self.assertIs(batch, self.conn.schedule_batch_pending)

    async def test_date_can_be_corrected_while_completing_fields(self):
        await respond(self.conn, '明天中午12点提醒我吃药，32号下午16点提醒我去医院')
        self.commands.assert_not_awaited()
        await respond(self.conn, '后天下午16点')
        self.assertEqual(ms('2026-10-02T16:00:00'), self.bodies()[1]['triggerAt'])

    async def test_partial_timeout_retry_skips_saved_entries_and_reuses_failed_id(self):
        async def fail_second(device, body):
            if body['title'] == '去医院':
                raise TimeoutError()
            return self.receipt(device, body)
        self.commands.side_effect = fail_second
        answer = await respond(self.conn, REQUEST+'，后天18点提醒我喝水')
        self.assertIn('已经确认存好的有：第1条', answer)
        self.assertIn('第2条有没有存好，我还没确认', answer)
        self.assertIn('后面1条还没设置', answer)
        self.assertEqual(2, len(self.bodies()))
        first, failed = [dict(x) for x in self.bodies()]
        self.commands.side_effect = self.receipt
        self.assertIn('这三条提醒都记好了', await respond(self.conn, '重试'))
        self.assertEqual(failed, self.bodies()[2])
        self.assertEqual('喝水', self.bodies()[3]['title'])
        self.assertEqual(1, sum(x['requestId'] == first['requestId'] for x in self.bodies()))

    async def test_bad_receipt_is_not_reported_as_saved(self):
        self.commands.side_effect = None
        self.commands.return_value = dict(id='wrong', title='吃药', status='scheduled')
        answer = await respond(self.conn, REQUEST)
        self.assertIn('目前还没有确认存好', answer)
        self.assertIn('第1条有没有存好，我还没确认', answer)
        self.assertNotIn('提醒都记好了', answer)
        self.commands.assert_awaited_once()

    async def test_unrelated_answer_and_session_switch_cannot_create(self):
        await respond(self.conn, '明天中午12点提醒我吃药，3号提醒我去医院')
        self.assertIsNone(await handle(self.conn, '我下午出去玩'))
        self.commands.assert_not_awaited()
        self.conn.session_id = 'new-session'
        self.assertIn('已失效', await handle(self.conn, '16点'))
        self.commands.assert_not_awaited()

    async def test_cancel_before_submit_does_not_leave_partial_records(self):
        await respond(self.conn, '明天中午12点提醒我吃药，3号提醒我去医院')
        self.assertIn('先不设了', await respond(self.conn, '取消设置'))
        self.commands.assert_not_awaited()
        self.assertIsNone(self.conn.schedule_batch_pending)

    async def test_quoted_schedule_is_preserved_as_single_title(self):
        title = '看《3号下午16点提醒我去医院》'
        await respond(self.conn, '明天16点提醒我'+title)
        self.commands.assert_awaited_once()
        self.assertEqual(title, self.bodies()[0]['title'])

    async def test_recurring_and_once_clauses_keep_separate_rules(self):
        await respond(self.conn, '每天中午12点提醒我吃药，3号16点提醒我去医院')
        self.assertEqual(['daily', 'once'], [x['recurrence'] for x in self.bodies()])

    async def test_more_than_five_entries_is_rejected_without_writes(self):
        text = '，'.join(f'明天{14+i}点提醒我事项{i}' for i in range(6))
        self.assertIn('最多设置5条', await respond(self.conn, text))
        self.commands.assert_not_awaited()

    async def test_ambiguous_clause_boundary_does_not_store_compound_title(self):
        answer = await respond(self.conn, '明天16点提醒我吃药以及去医院提醒我带证件')
        self.assertIn('分别发送', answer)
        self.commands.assert_not_awaited()
