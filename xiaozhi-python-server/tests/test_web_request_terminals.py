import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from core.conversation.requests import WebChatRequest, response_error, response_progress, finish_text_reply


class WebTerminalTests(unittest.TestCase):
    def setUp(self):
        self.conn=NS(session_id='session',sentence_id='reply',pending_chassis_action={'sentence_id':'reply'})
        self.record=WebChatRequest('id',('text','device','session'),self.conn,'device')
        self.record.session='session'; self.record.sentence='reply'; self.conn.web_chat_tracking=self.record

    def test_tts_error_finishes_tracking_as_unknown_and_cancels_unstarted_motion(self):
        response_error(self.conn,'reply')
        self.assertEqual('unknown',self.record.status)
        self.assertIsNone(self.conn.pending_chassis_action)
        response_progress(self.conn,'reply',finished=True)
        self.assertEqual('unknown',self.record.status)

    def test_old_sentence_session_or_unbound_error_cannot_change_current_request(self):
        response_error(self.conn,'old'); response_error(self.conn,None)
        self.assertEqual('accepted',self.record.status)
        self.record.session='old-session'; response_error(self.conn,'reply')
        self.assertEqual('accepted',self.record.status)

    def test_transcript_only_finishes_without_tts_last(self):
        finish_text_reply(self.conn)
        self.assertEqual('server_done',self.record.status)

    def test_file_provider_without_output_stops_after_five_attempts(self):
        from core.providers.tts.base import TTSProviderBase
        class Provider(TTSProviderBase):
            async def text_to_speak(self,text,path): return None
        provider=Provider.__new__(Provider)
        provider.delete_audio_file=False; provider._correct_words_pattern=None
        provider.generate_filename=Mock(return_value='unused-test-output')
        provider._run_async_with_timeout=Mock(return_value=None); provider.tts_timeout=1
        with patch('core.providers.tts.base.os.path.exists',return_value=False):
            self.assertIsNone(provider.to_tts('测试'))
        self.assertEqual(5,provider._run_async_with_timeout.call_count)
