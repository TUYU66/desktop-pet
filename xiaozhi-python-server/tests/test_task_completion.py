"""Todo removal regressions: the shared entry never queries or mutates todos."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from core.reminders.conversation import respond, relevant


class TodoRetirementTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_todo_requests_report_removal_without_any_backend_operation(self):
        conn = NS(headers={'device-id': 'device'}, session_id='session', logger=Mock())
        with patch('core.reminders.legacy.todo_client.state', AsyncMock()) as state, patch('core.reminders.legacy.todo_client.command', AsyncMock()) as command, patch('core.reminders.client.state', AsyncMock()) as reminders:
            for text in ('今天有哪些待办', '添加任务整理桌面', '把整理桌面的待办标记完成', '查询任务清单'):
                self.assertTrue(relevant(text))
                self.assertIn('待办功能已移除', await respond(conn, text))
            state.assert_not_awaited()
            command.assert_not_awaited()
            reminders.assert_not_awaited()

    async def test_natural_question_or_completion_never_reaches_retired_todo_handler(self):
        conn = NS(headers={'device-id': 'device'}, session_id='session', logger=Mock())
        with patch('core.reminders.legacy.todo_client.state', AsyncMock()) as state, patch('core.reminders.legacy.todo_client.command', AsyncMock()) as command:
            for text in ('我收拾好桌面了吗', '拿到快递了吗', '我收拾好桌面了'):
                self.assertIsNone(await respond(conn, text))
            state.assert_not_awaited()
            command.assert_not_awaited()
