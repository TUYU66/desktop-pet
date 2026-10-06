"""Web music controls must not wait for a completed reply's task cleanup."""
import asyncio
import ast
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.api.chat_handler import ChatHandler
from core.conversation.requests import WebChatRequest, finish_text_reply, response_progress


class MusicScheduleRoutingTests(unittest.TestCase):
    def test_schedule_receives_only_the_request_after_music_control(self):
        # Load the real chat method without initializing hardware or LLM providers.
        source=Path(__file__).parents[1]/'core'/'conversation'/'engine.py'
        tree=ast.parse(source.read_text(encoding='utf-8-sig'))
        owner=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='ConversationEngine')
        method=next(node for node in owner.body if isinstance(node,ast.FunctionDef) and node.name=='_chat')
        namespace={'time':__import__('time')}
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),namespace)
        for text,expected in (
                ('暂停音乐，然后明天下午三点提醒我喝水','明天下午三点提醒我喝水'),
                ('停止音乐，然后查看今天日程','查看今天日程'),
                ('暂停播放然后把明天拿快递延后两小时','把明天拿快递延后两小时'),
                ('明天下午三点提醒我喝水','明天下午三点提醒我喝水')):
            with self.subTest(text=text), \
                    patch('core.conversation.standby.queue_exit_reply',return_value=False), \
                    patch('core.reminders.conversation.handle_sync',return_value=True) as schedule:
                conn=SimpleNamespace()
                self.assertTrue(namespace['_chat'](conn,text))
                schedule.assert_called_once_with(conn,expected)


class WebMusicRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = SimpleNamespace(
            stop_event=threading.Event(), session_id='session', sentence_id='music-stream',
            standby=False, chat_lock=threading.Lock(), client_abort=False,
            client_is_speaking=True,
            local_music=SimpleNamespace(state='playing', awaiting_sentence='reply',
                command=AsyncMock(return_value={'name': 'song', 'volume': 70})))
        with patch('core.api.chat_handler.setup_logging', return_value=Mock()):
            self.api = ChatHandler({}, SimpleNamespace(device_handlers={'device': self.conn}))
        self.previous = WebChatRequest('previous', ('播放音乐', 'device', ''), self.conn, 'device')
        self.previous.session = 'session'
        self.previous.sentence = 'reply'
        self.previous.update('responding', '正在处理回复')
        self.previous.task = asyncio.create_task(asyncio.Event().wait())
        self.conn.web_chat_tracking = self.previous
        self.record = WebChatRequest('pause', ('暂停播放', 'device', ''), self.conn, 'device')
        self.record.task = asyncio.current_task()

    async def asyncTearDown(self):
        self.previous.task.cancel()
        await asyncio.gather(self.previous.task, return_exceptions=True)

    async def pause(self, text='暂停播放'):
        async def pause_audio(conn, for_chat=True):
            conn.local_music.state = 'paused'
            conn.client_is_speaking = False

        async def finish_confirmation(conn, text, standby=True, sentence_id=None):
            response_progress(conn, sentence_id, finished=True)

        with patch('core.music.conversation.pause_for_chat', side_effect=pause_audio), \
                patch('core.music.conversation.say', side_effect=finish_confirmation), \
                patch('core.handle.reportHandle.enqueue_asr_report', Mock()):
            await self.api.execute(self.record, text, '')

    async def test_playing_own_song_releases_previous_request_before_task_exits(self):
        self.assertFalse(self.previous.task.done())
        await self.pause()
        self.conn.local_music.command.assert_awaited_once()
        self.assertEqual(self.conn.local_music.command.call_args.args, ('pause',))
        self.assertEqual(self.previous.status, 'server_done')
        self.assertEqual(self.record.status, 'server_done')
        self.assertIsNone(self.conn.web_chat_tracking)
        self.assertFalse(self.conn.chat_lock.locked())

    async def test_finished_reply_allows_control_while_waiter_is_still_running(self):
        self.previous.reply_finished.set()
        self.conn.local_music.awaiting_sentence = None
        await self.pause()
        self.assertEqual(self.record.status, 'server_done')
        self.conn.local_music.command.assert_awaited_once()

    async def test_natural_pause_consumes_pending_chat_question(self):
        import time
        self.conn.music_chat_pending = {'text': '聊聊今天', 'session': 'session', 'at': time.monotonic()}
        await self.pause('暂停吧，下次再听我要去吃饭了')
        self.assertEqual(self.conn.local_music.command.call_args.args, ('pause',))
        self.assertEqual(self.record.status, 'server_done')
        self.assertIsNone(self.conn.music_chat_pending)

    async def test_web_pause_then_motion_keeps_full_text_and_reaches_chat_after_pause(self):
        from concurrent.futures import Future
        text = '暂停播放然后站起来'
        self.conn.run_web_chat = Mock()

        async def pause_audio(conn, for_chat=True):
            conn.local_music.state = 'paused'
            conn.client_is_speaking = False

        def submit(function, query, record):
            self.assertEqual(self.conn.local_music.state, 'paused')
            self.assertEqual(query, text)
            self.assertIs(function, self.conn.run_web_chat)
            record.sentence = 'action-reply'
            self.conn.sentence_id = record.sentence
            response_progress(self.conn, record.sentence, finished=True)
            self.conn.chat_lock.release()  # Same ownership transfer as run_web_chat.
            future = Future()
            future.set_result(True)
            return future

        self.conn.executor = SimpleNamespace(submit=Mock(side_effect=submit))
        send_stt = AsyncMock()
        with patch('core.music.conversation.pause_for_chat', side_effect=pause_audio), \
                patch('core.music.pause_for_chat', side_effect=pause_audio), \
                patch('core.handle.reportHandle.enqueue_tts_report', Mock()) as report_reply, \
                patch.dict('sys.modules', {'core.handle.sendAudioHandle': SimpleNamespace(send_stt_message=send_stt)}):
            await self.api.execute(self.record, text, '')
        self.assertEqual(self.record.status, 'server_done')
        self.conn.executor.submit.assert_called_once()
        send_stt.assert_awaited_once_with(self.conn, text)
        report_reply.assert_not_called()
        self.conn.local_music.command.assert_not_awaited()
        self.assertFalse(self.conn.chat_lock.locked())

    async def test_text_reply_does_not_complete_another_session(self):
        self.previous.session = 'other-session'
        finish_text_reply(self.conn)
        self.assertEqual(self.previous.status, 'responding')
        self.assertFalse(self.previous.reply_finished.is_set())

    async def test_loading_song_does_not_override_unfinished_reply(self):
        self.conn.local_music.state = 'loading'
        await self.pause()
        self.assertEqual(self.record.status, 'failed')
        self.conn.local_music.command.assert_not_awaited()
        self.assertIs(self.conn.web_chat_tracking, self.previous)

    async def test_unrelated_song_does_not_override_unfinished_reply(self):
        self.conn.local_music.awaiting_sentence = 'another-reply'
        await self.pause()
        self.assertEqual(self.record.status, 'failed')
        self.conn.local_music.command.assert_not_awaited()

    async def test_other_session_does_not_override_unfinished_reply(self):
        self.previous.session = 'other-session'
        await self.pause()
        self.assertEqual(self.record.status, 'failed')
        self.conn.local_music.command.assert_not_awaited()

    async def test_last_publishes_completion_without_waiting_for_request_task(self):
        response_progress(self.conn, 'older-reply', finished=True)
        self.assertFalse(self.previous.reply_finished.is_set())
        response_progress(self.conn, 'reply', finished=True)
        self.assertEqual(self.previous.status, 'server_done')
        self.assertTrue(self.previous.reply_finished.is_set())
        self.assertFalse(self.previous.task.done())

    async def test_stuck_music_handoff_finishes_tracker_instead_of_processing_forever(self):
        self.previous.reply_finished.set()
        wait_for = asyncio.wait_for
        async def stuck(conn, text, web=False): await asyncio.Event().wait()
        def bounded(operation, timeout):
            return wait_for(operation, .01 if timeout == 45 else timeout)
        with patch('core.music.conversation.handle_input', side_effect=stuck), \
                patch('core.api.chat_handler.asyncio.wait_for', side_effect=bounded):
            await self.api.execute(self.record, '下一首', '')
        self.assertEqual(self.record.status, 'unknown')
        self.assertIsNone(self.conn.web_chat_tracking)
        self.conn.local_music.command.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
