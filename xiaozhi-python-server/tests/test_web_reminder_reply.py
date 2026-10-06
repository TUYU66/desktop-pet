"""An optional reminder cannot turn a completed main reply into unknown."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from core.api.chat_handler import ChatHandler
from core.conversation.requests import WebChatRequest, followup_progress, response_error, finish_optional_followup
from core.handle.sendAudioHandle import sendAudioMessage
from core.providers.tts.dto.dto import SentenceType
from test_reminder_handler import connection


class WebReminderReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = connection()
        with patch('core.api.chat_handler.setup_logging', return_value=Mock()):
            self.api = ChatHandler({}, SimpleNamespace(device_handlers={'device': self.conn}))
        self.record = WebChatRequest('web', ('hi', 'device', ''), self.conn, 'device')
        self.record.session = self.conn.session_id
        self.record.sentence = self.conn.sentence_id
        self.conn.web_chat_tracking = self.record

    async def test_optional_notice_abort_preserves_successful_main_reply(self):
        followup_progress(self.conn, 'old')
        self.conn.client_abort = True
        await self.api.await_reply(self.record)
        self.assertEqual(self.record.status, 'server_done')

    async def test_main_reply_and_uncertain_physical_action_still_require_verification(self):
        self.conn.client_abort = True
        await self.api.await_reply(self.record)
        self.assertEqual(self.record.status, 'unknown')
        another = WebChatRequest('action', ('左转', 'device', ''), self.conn, 'device')
        another.session = self.conn.session_id; another.sentence = 'old'
        another.action_unconfirmed = True
        self.conn.web_chat_tracking = another
        followup_progress(self.conn, 'old')
        await self.api.await_reply(another)
        self.assertEqual(another.status, 'unknown')

    async def test_error_of_optional_speech_does_not_erase_main_reply_receipt(self):
        followup_progress(self.conn, 'old')
        response_error(self.conn, 'old')
        self.assertEqual(self.record.status, 'server_done')
        self.assertTrue(self.record.reply_finished.is_set())

    async def test_explicit_stop_of_optional_notice_preserves_main_receipt(self):
        followup_progress(self.conn, 'old')
        self.assertTrue(finish_optional_followup(self.record))
        self.assertEqual(self.record.status, 'server_done')
        self.record.action_unconfirmed = True
        self.assertFalse(finish_optional_followup(self.record))

    async def test_interrupted_old_last_cannot_stop_new_turn_even_with_same_sentence(self):
        self.conn.tts.tts_audio_first_sentence = False
        self.conn.close_after_chat = False
        self.conn.input_generation = 1
        async def change_turn(conn, sentence):
            conn.input_generation += 1
            conn.client_abort = False  # A new interaction already reset abort.
        with patch('core.reminders.followup.deliver', new=AsyncMock(side_effect=change_turn)), \
                patch('core.handle.sendAudioHandle.sendAudio', new=AsyncMock()), \
                patch('core.handle.sendAudioHandle.send_tts_message', new=AsyncMock()) as send:
            await sendAudioMessage(self.conn, SentenceType.LAST, [], None, 'old')
        send.assert_not_awaited()
        self.assertFalse(self.conn.tts_finishing)
        self.assertEqual(self.record.status, 'accepted')
