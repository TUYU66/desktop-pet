"""Moved-date queries preserve occurrence identity and ignore automatic retry dates."""
import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from core.reminders.parser import ZONE
from core.reminders.conversation import respond
from core.reminders.edits import respond_edit


def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=ZONE).timestamp()*1000)


class ArrangedDateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = NS(headers={'device-id': 'device'}, session_id='session', logger=Mock())
        self.today = ms('2026-09-30T15:00:00')
        self.tomorrow = ms('2026-10-01T15:00:00')
        self.item = dict(id='item', title='拿快递', version=3, status='scheduled',
                         dueAt=self.tomorrow, scheduledAt=self.tomorrow, originalDueAt=self.today)
        self.snapshot = dict(items=[self.item], plans=[], serverNow=ms('2026-09-30T13:00:00'))
        self.state = AsyncMock(return_value=self.snapshot)
        self.command = AsyncMock()
        for name, mock in [('state', self.state), ('command', self.command)]:
            p = patch('core.reminders.client.'+name, mock)
            p.start()
            self.addCleanup(p.stop)

    async def test_rescheduled_item_is_only_listed_on_new_day(self):
        self.assertIn('没有日程提醒', await respond(self.conn, '今天日程'))
        answer = await respond(self.conn, '明天日程')
        self.assertIn('拿快递', answer)
        self.assertIn('明天', answer)
        self.command.assert_not_awaited()

    async def test_automatic_retry_does_not_move_users_arranged_day(self):
        self.item.update(status='retry_pending', scheduledAt=self.today, dueAt=self.tomorrow)
        self.assertIn('拿快递', await respond(self.conn, '今天日程'))
        self.assertIn('没有日程提醒', await respond(self.conn, '明天日程'))

    async def test_moved_cycle_occurrence_is_not_projected_again_on_old_day(self):
        self.item['planId'] = 'plan'
        self.snapshot['plans'] = [dict(id='plan', title='拿快递', status='active', version=4,
            recurrence='daily', initialAt=self.today, nextAt=self.today)]
        self.assertIn('没有日程提醒', await respond(self.conn, '今天日程'))
        # The moved occurrence and tomorrow's independent occurrence both exist.
        self.assertIn('明天', await respond(self.conn, '明天日程'))

    async def test_completed_moved_occurrence_also_blocks_old_day_projection(self):
        self.item.update(planId='plan', status='completed')
        self.snapshot['plans'] = [dict(id='plan', title='拿快递', status='active', version=4,
            recurrence='daily', initialAt=self.today, nextAt=self.today)]
        self.assertIn('没有日程提醒', await respond(self.conn, '今天日程'))

    async def test_relative_edit_uses_arranged_time_after_automatic_retry(self):
        self.item.update(scheduledAt=self.today, dueAt=self.tomorrow, status='retry_pending')
        self.command.side_effect = lambda device, body: dict(self.item, status='scheduled', version=4,
            dueAt=body['triggerAt'], scheduledAt=body['triggerAt'])
        self.assertIn('改到', await respond_edit(self.conn, '今天拿快递提醒延后两小时'))
        self.assertEqual(self.today+7200000, self.command.call_args.args[1]['triggerAt'])

    async def test_single_edit_multiple_field_answers_are_retained(self):
        await respond(self.conn, '拿快递提醒改到后天')
        self.assertIn('上午还是下午', await respond(self.conn, '三点'))
        self.command.assert_not_awaited()
        self.command.side_effect = lambda device, body: dict(self.item, status='scheduled', version=4,
            dueAt=body['triggerAt'], scheduledAt=body['triggerAt'])
        self.assertIn('改到', await respond(self.conn, '下午'))
        self.assertEqual(ms('2026-10-02T15:00:00'), self.command.call_args.args[1]['triggerAt'])
