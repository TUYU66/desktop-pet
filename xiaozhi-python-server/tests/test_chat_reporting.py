"""Reporting port works without a WebSocket connection or model runtime."""
import asyncio
import json
import unittest
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from core.reporting.chat_reports import enqueue_asr_report, enqueue_tts_report, get_report_session_id, process_report


class ChatReportingTests(unittest.TestCase):
    def context(self, history=1):
        return SimpleNamespace(
            config={'activeRole': 'default', 'roles': '[{"id":"default","name":"小兰"}]'},
            device_id='device', session_id='local-session', logger=Mock(),
            report_enabled=True, report_asr_enable=True, report_tts_enable=True,
            chat_history_conf=history, report_queue=Queue(),
        )

    def test_history_policy_controls_audio_and_disabled_reporting(self):
        conn = self.context()
        enqueue_asr_report(conn, '你好', [b'audio'])
        self.assertEqual((1, '你好', None), conn.report_queue.get()[:3])
        conn.chat_history_conf = 2
        enqueue_tts_report(conn, '我在', [b'audio'])
        self.assertEqual((2, '我在', [b'audio']), conn.report_queue.get()[:3])
        conn.report_enabled = False
        enqueue_asr_report(conn, '不保存', [])
        self.assertTrue(conn.report_queue.empty())

    def test_role_session_and_report_use_private_service_identity(self):
        conn = self.context()
        response = Mock(status_code=200)
        response.json.return_value = {'data': {'sessionId': 'java-role-session'}}
        with patch.dict('os.environ', {'XIAOZHI_PYTHON_SERVICE_KEY': 'test-private'}), \
                patch('httpx.get', return_value=response) as get, \
                patch('httpx.post', return_value=Mock(status_code=200)) as post:
            process_report(conn, 1, '你好', None, 123)
        self.assertEqual('小兰', get.call_args.kwargs['params']['roleName'])
        self.assertEqual('java-role-session', post.call_args.kwargs['json']['sessionId'])
        for call in (get.call_args, post.call_args):
            self.assertEqual('test-private', call.kwargs['headers']['Service-Key'])

    def test_unavailable_java_falls_back_to_existing_session(self):
        conn = self.context()
        with patch('httpx.get', side_effect=TimeoutError):
            self.assertEqual('local-session', get_report_session_id(conn))


class PlaybackReportingTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        conn = ChatReportingTests().context()
        conn.sentence_id = 'reply'
        conn.client_abort = False
        conn.websocket = SimpleNamespace(send=AsyncMock())
        conn.tts = SimpleNamespace(tts_audio_first_sentence=False, report_on_last=False)
        return conn

    async def test_future_segment_is_not_published_until_paced_marker(self):
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        from core.utils.audioRateController import AudioRateController
        conn = self.context()
        conn.audio_rate_controller = AudioRateController(60)
        conn.audio_rate_controller.pending_send_task = asyncio.get_running_loop().create_future()
        conn.audio_flow_control = {'sentence_id': 'reply'}
        conn.audio_rate_controller.add_audio(b'previous segment audio')
        await sendAudioMessage(conn, SentenceType.FIRST, None, '下一段语音', 'reply', report_text=True)
        self.assertTrue(conn.report_queue.empty())
        conn.websocket.send.assert_not_awaited()
        async def sent(packet):
            self.assertTrue(conn.report_queue.empty())
        await conn.audio_rate_controller.check_queue(sent)
        self.assertEqual((2, '下一段语音', None), conn.report_queue.get_nowait()[:3])
        self.assertTrue(conn.report_queue.empty())

    async def test_interruption_or_new_reply_discards_unspoken_marker(self):
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        from core.utils.audioRateController import AudioRateController
        for interrupt in ('abort', 'new_reply'):
            conn = self.context()
            conn.audio_rate_controller = AudioRateController(60)
            conn.audio_rate_controller.pending_send_task = asyncio.get_running_loop().create_future()
            conn.audio_flow_control = {'sentence_id': 'reply'}
            await sendAudioMessage(conn, SentenceType.FIRST, None, '还没有说的话', 'reply', report_text=True)
            if interrupt == 'abort':
                conn.client_abort = True
            else:
                conn.sentence_id = 'new'
            await conn.audio_rate_controller.check_queue(AsyncMock())
            self.assertTrue(conn.report_queue.empty())
            conn.websocket.send.assert_not_awaited()

    async def test_first_segment_reports_once_and_legacy_audio_history_stays_separate(self):
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        for history, accumulated in ((1, False), (2, False), (1, True)):
            conn = self.context()
            conn.chat_history_conf = history
            conn.tts.report_on_last = accumulated
            await sendAudioMessage(conn, SentenceType.FIRST, None, '现在开始说', 'reply', report_text=True)
            conn.websocket.send.assert_awaited_once()
            self.assertEqual(history == 1 and not accumulated, not conn.report_queue.empty())

    async def test_chassis_result_after_stopped_sender_keeps_subtitle_and_history(self):
        from threading import Event
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        from core.utils.audioRateController import AudioRateController
        conn = self.context()
        conn.stop_event = Event()
        conn.audio_rate_controller = AudioRateController(60)
        conn.audio_flow_control = {'sentence_id': 'reply', 'packet_count': 1}
        # The first spoken acknowledgement completed, but the action's result
        # reuses the same reply ID. Cover completed and cancellation-pending tasks.
        for cancelling in (False, True):
            old_sender = asyncio.create_task(asyncio.sleep(60))
            conn.audio_rate_controller.pending_send_task = old_sender
            conn.audio_rate_controller.stop_sending()
            if not cancelling:
                await asyncio.gather(old_sender, return_exceptions=True)
            conn.websocket.send.reset_mock()
            with patch('core.handle.sendAudioHandle._do_send_audio', new_callable=AsyncMock):
                await sendAudioMessage(conn, SentenceType.FIRST, None, '站起来啦', 'reply', report_text=True)
                await sendAudioMessage(conn, SentenceType.MIDDLE, b'outcome audio', None, 'reply')
            self.assertEqual((2, '站起来啦', None), conn.report_queue.get_nowait()[:3])
            self.assertTrue(conn.report_queue.empty())
            message = json.loads(conn.websocket.send.call_args.args[0])
            self.assertEqual(('sentence_start', '站起来啦'), (message['state'], message['text']))
            sender = conn.audio_rate_controller.pending_send_task
            self.assertIsNot(sender, old_sender)
            self.assertFalse(conn.audio_rate_controller.sending_stopped)
            conn.audio_rate_controller.stop_sending()
            await asyncio.gather(sender, old_sender, return_exceptions=True)
