"""Reporting port works without a WebSocket connection or model runtime."""
import unittest
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch
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
