"""Web action receipts distinguish waiting, speech, and unconfirmed completion."""
import unittest
from types import SimpleNamespace

from core.conversation.requests import (WebChatRequest, action_progress,
                                   response_progress, finish_text_reply)


class ChassisRequestTrackingTests(unittest.TestCase):
    def setUp(self):
        self.conn = SimpleNamespace(session_id='session', sentence_id='reply')
        self.record = WebChatRequest('request', ('左转', '', ''), self.conn, 'device')
        self.record.session = 'session'
        self.record.sentence = 'reply'
        self.conn.web_chat_tracking = self.record

    def test_audio_progress_does_not_hide_pending_action_confirmation(self):
        action_progress(self.conn, 'reply', waiting=True)
        response_progress(self.conn, 'reply')
        self.assertEqual(self.record.status, 'waiting_action')
        self.assertFalse(self.record.reply_finished.is_set())
        action_progress(self.conn, 'reply', waiting=False)
        response_progress(self.conn, 'reply', finished=True)
        self.assertEqual(self.record.status, 'server_done')

    def test_unconfirmed_result_finishes_after_feedback_without_claiming_success(self):
        action_progress(self.conn, 'reply', waiting=False, unconfirmed=True)
        self.assertEqual(self.record.status, 'responding')
        self.assertFalse(self.record.reply_finished.is_set())
        response_progress(self.conn, 'reply', finished=True)
        self.assertEqual(self.record.status, 'unknown')
        self.assertTrue(self.record.reply_finished.is_set())
        action_progress(self.conn, 'reply', waiting=False)
        self.assertEqual(self.record.status, 'unknown')

    def test_old_action_does_not_change_a_new_sentence_or_connection(self):
        action_progress(self.conn, 'older', waiting=True, unconfirmed=True)
        self.assertEqual(self.record.status, 'accepted')
        self.conn.session_id = 'new-session'
        action_progress(self.conn, 'reply', waiting=False, unconfirmed=True)
        self.assertFalse(self.record.action_unconfirmed)

    def test_transcript_only_unknown_result_also_closes_tracking(self):
        action_progress(self.conn, 'reply', waiting=False, unconfirmed=True)
        finish_text_reply(self.conn)
        self.assertEqual(self.record.status, 'unknown')
        self.assertTrue(self.record.reply_finished.is_set())


if __name__ == '__main__':
    unittest.main()
