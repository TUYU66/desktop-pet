import unittest
import time
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from core.reminders.conversation import respond
from core.reminders.intent import is_reminder_request


class CallIntentTests(unittest.IsolatedAsyncioTestCase):
    def conn(self):
        return SimpleNamespace(llm=Mock(), logger=Mock(), headers={'device-id': 'device'}, schedule_pending=None)

    async def test_naming_never_reads_or_writes_schedule(self):
        for text in ('叫哥哥太小了，叫叔叔太老了，那你应该叫我什么', '以后叫我小王', '你明天也要叫我老师'):
            conn = self.conn()
            conn.llm.response_json.return_value = '{"isReminderRequest":false}'
            with patch('core.reminders.client.state', new_callable=AsyncMock) as state, patch('core.reminders.client.command', new_callable=AsyncMock) as command:
                self.assertIsNone(await respond(conn, text))
                state.assert_not_awaited()
                command.assert_not_awaited()
                self.assertIsNone(conn.schedule_pending)

    async def test_unrelated_naming_cannot_fill_pending(self):
        conn = self.conn()
        pending = {'text':'提醒我喝水', 'expires':time.monotonic()+60, 'action':'create'}
        conn.schedule_pending = pending
        conn.llm.response_json.return_value = '{"isReminderRequest":false}'
        with patch('core.reminders.client.state', new_callable=AsyncMock) as state:
            self.assertIsNone(await respond(conn, '那你应该叫我什么'))
            state.assert_not_awaited()
            self.assertIs(conn.schedule_pending, pending)

    async def test_real_wake_request_reaches_time_clarification(self):
        conn = self.conn()
        conn.llm.response_json.return_value = '{"isReminderRequest":true}'
        with patch('core.reminders.client.state', new_callable=AsyncMock, return_value={'serverNow':1789372800000,'items':[],'plans':[]}):
            self.assertIn('几点', await respond(conn, '明天叫我起床'))
            self.assertEqual(conn.schedule_pending['action'], 'create')

    async def test_malformed_or_failed_classifier_does_not_start_schedule(self):
        conn = self.conn()
        for value in ('{}', '{"isReminderRequest":"false"}', 'bad json'):
            conn.llm.response_json.return_value = value
            self.assertIsNone(await respond(conn, '叫我小王'))
            self.assertIsNone(conn.schedule_pending)
        conn.llm.response_json.side_effect = TimeoutError()
        self.assertIsNone(await respond(conn, '叫我小王'))


if __name__ == '__main__':
    unittest.main()
