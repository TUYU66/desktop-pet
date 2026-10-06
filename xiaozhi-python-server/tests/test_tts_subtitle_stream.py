import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.handle.sendAudioHandle import sendAudioMessage, send_stt_message, send_tts_message
from core.providers.tts.dto.dto import SentenceType
from core.utils.textUtils import normalize_spoken_text


class SubtitleStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_recognition_displays_text_without_claiming_audio_started(self):
        conn = self.connection()
        conn.config = {}
        conn.client_is_speaking = False
        await send_stt_message(conn, '明天提醒我取快递')
        conn.websocket.send.assert_awaited_once()
        self.assertEqual(json.loads(conn.websocket.send.call_args.args[0])['type'], 'stt')
        self.assertFalse(conn.client_is_speaking)

    def connection(self):
        return NS(sentence_id='old', session_id='session', logger=Mock(),
                  websocket=NS(send=AsyncMock()), tts=NS(tts_audio_first_sentence=True))

    async def test_real_reply_start_precedes_subtitle_and_uses_new_id(self):
        conn = self.connection()
        await send_tts_message(conn, 'start')  # Early thinking notification.
        conn.sentence_id = 'reply'
        events = []

        async def record_message(data):
            events.append(json.loads(data))

        async def record_audio(*args):
            events.append({'type': 'audio'})

        conn.websocket.send.side_effect = record_message
        with patch('core.handle.sendAudioHandle.sendAudio', side_effect=record_audio):
            await sendAudioMessage(conn, SentenceType.FIRST, None, '行，那我给你讲个短的。\n\n有个老头住在海边', 'reply')
            await sendAudioMessage(conn, SentenceType.FIRST, None, '每天傍晚去码头', 'reply')
        self.assertEqual(['start', 'sentence_start', None, 'sentence_start', None],
                         [event.get('state') for event in events])
        self.assertEqual('reply', events[0]['stream_id'])
        self.assertEqual('reply', events[1]['stream_id'])
        self.assertEqual('行，那我给你讲个短的。 有个老头住在海边', events[1]['text'])
        self.assertFalse(conn.tts.tts_audio_first_sentence)

    async def test_old_reply_does_not_start_or_change_current_stream(self):
        conn = self.connection()
        conn.sentence_id = 'reply'
        with patch('core.handle.sendAudioHandle.sendAudio', new_callable=AsyncMock) as audio:
            await sendAudioMessage(conn, SentenceType.FIRST, None, 'old subtitle', 'old')
        conn.websocket.send.assert_not_awaited()
        audio.assert_not_awaited()
        self.assertTrue(conn.tts.tts_audio_first_sentence)

    def test_line_break_cleanup_preserves_word_boundaries(self):
        self.assertEqual('hello world', normalize_spoken_text('hello\r\n\r\n  world'))
        self.assertEqual('短回复', normalize_spoken_text('短回复'))
