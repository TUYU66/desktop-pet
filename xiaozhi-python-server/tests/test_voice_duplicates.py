"""Wake control/audio overlap and music reporting through production ingress."""
import json
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.providers.asr.base import ASRProviderBase
from core.providers.tts.base import TTSProviderBase
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType


class Asr(ASRProviderBase):
    async def speech_to_text(self, *args, **kwargs):
        return '', None


class VoiceDuplicateTests(unittest.IsolatedAsyncioTestCase):
    def connection(self):
        return NS(input_generation=1, session_id='session', audio_format='opus',
                  stop_event=threading.Event(), standby=False, features={'asr_status': True},
                  websocket=NS(send=AsyncMock()), config={}, max_output_size=0,
                  chat_lock=threading.Lock(), logger=Mock())

    async def recognize(self, conn, text):
        provider = Asr()
        provider.speech_to_text_wrapper = AsyncMock(return_value=(text, None))
        await provider.handle_voice_stop(conn, [b'audio'])

    async def test_wake_echo_is_consumed_once_without_interrupting_greeting(self):
        conn = self.connection()
        conn.pending_wake_echo = ('小兰小兰', time.monotonic(), conn.session_id)
        with patch('core.providers.asr.base.startToChat', new_callable=AsyncMock) as route, \
                patch('core.providers.asr.base.enqueue_asr_report') as report:
            await self.recognize(conn, '小兰小兰。')
            route.assert_not_awaited()
            report.assert_not_called()
            self.assertEqual('done', json.loads(conn.websocket.send.await_args.args[0])['state'])
            await self.recognize(conn, '小兰小兰。')
            route.assert_awaited_once()
            report.assert_called_once()

    async def test_first_real_question_and_later_repetition_are_preserved(self):
        for text, age, session in [('小兰小兰，今天天气怎么样？', 0, 'session'),
                                   ('小兰小兰。', 9, 'session'),
                                   ('小兰小兰。', 0, 'previous')]:
            with self.subTest(text=text, age=age, session=session):
                conn = self.connection()
                conn.pending_wake_echo = ('小兰小兰', time.monotonic() - age, session)
                with patch('core.providers.asr.base.startToChat', new_callable=AsyncMock) as route, \
                        patch('core.providers.asr.base.enqueue_asr_report') as report:
                    await self.recognize(conn, text)
                    route.assert_awaited_once_with(conn, text)
                    report.assert_called_once()

    async def test_pause_has_one_report_per_recognition_even_when_repeated(self):
        conn = self.connection()
        conn.local_music = NS(state='paused', command=AsyncMock(return_value={}))
        with patch('core.providers.asr.base.enqueue_asr_report') as ingress_report, \
                patch('core.reporting.chat_reports.enqueue_asr_report') as music_report, \
                patch('core.music.conversation.pause_for_chat', new_callable=AsyncMock), \
                patch('core.music.conversation.say', new_callable=AsyncMock), \
                patch('core.handle.receiveAudioHandle.send_stt_message', new_callable=AsyncMock):
            await self.recognize(conn, '暂停播放。')
            await self.recognize(conn, '暂停播放。')
            self.assertEqual(2, ingress_report.call_count)
            self.assertEqual(2, conn.local_music.command.await_count)
            music_report.assert_not_called()

    async def test_web_music_still_reports(self):
        from core.music.conversation import handle_input
        conn = self.connection()
        conn.local_music = NS(state='paused', command=AsyncMock(return_value={}))
        with patch('core.reporting.chat_reports.enqueue_asr_report') as report, \
                patch('core.music.conversation.pause_for_chat', new_callable=AsyncMock), \
                patch('core.music.conversation.say', new_callable=AsyncMock):
            handled, _ = await handle_input(conn, '暂停播放。', web=True)
            self.assertTrue(handled)
            report.assert_called_once_with(conn, '暂停播放。', [])

    async def test_device_detect_marks_custom_wake_before_asr(self):
        from core.handle.textHandler.listenMessageHandler import ListenTextMessageHandler
        conn = self.connection()
        conn.config = {'customWakeWord': '小兰小兰'}
        conn.reset_audio_states = Mock()
        with patch('core.music.pause_for_chat', new_callable=AsyncMock), \
                patch('core.handle.textHandler.listenMessageHandler.enqueue_asr_report') as report, \
                patch('core.handle.textHandler.listenMessageHandler.startToChat', new_callable=AsyncMock) as route:
            await ListenTextMessageHandler().handle(conn, {'state': 'detect', 'text': '小兰小兰'})
            self.assertEqual('小兰小兰', conn.pending_wake_echo[0])
            report.assert_called_once()
            route.assert_awaited_once_with(conn, '嘿，你好呀', display_text='小兰小兰')


class TtsBatchTests(unittest.TestCase):
    def provider(self):
        class Tts(TTSProviderBase):
            async def text_to_speak(self, text, path):
                return None
        provider = Tts({}, True)
        provider.conn = NS(config={})
        return provider

    def message(self, text='', sentence='s', kind=SentenceType.MIDDLE, content=ContentType.TEXT):
        return TTSMessageDTO(sentence, kind, content, text)

    def test_queued_clauses_merge_but_end_file_and_new_turn_keep_order(self):
        for boundary in [self.message(kind=SentenceType.LAST),
                         self.message(content=ContentType.FILE),
                         self.message(sentence='new', kind=SentenceType.FIRST)]:
            provider = self.provider()
            provider.tts_text_queue.put(self.message('后续内容。'))
            provider.tts_text_queue.put(boundary)
            tail = self.message('边界后内容。')
            provider.tts_text_queue.put(tail)
            self.assertIs(boundary, provider._collect_queued_text('s'))
            self.assertEqual(['后续内容。'], provider.tts_text_buff)
            self.assertIs(tail, provider.tts_text_queue.get_nowait())

    def test_opening_stays_short_and_available_following_clauses_share_request(self):
        provider = self.provider()
        clauses = ['这是已经准备好的第一段语音内容。',
                   '这是已经准备好的第二段语音内容。',
                   '这是已经准备好的第三段语音内容。']
        provider.tts_text_buff = clauses
        first = provider._get_segment_text()
        self.assertIn('第一段', first)
        self.assertNotIn('第二段', first)
        following = provider._get_segment_text()
        self.assertIn('第二段', following)
        self.assertIn('第三段', following)
        self.assertEqual(sum(map(len, clauses)), provider.processed_chars)

    def test_coalescing_is_bounded_and_does_not_wait_for_text(self):
        provider = self.provider()
        self.assertIsNone(provider._collect_queued_text('s'))
        for _ in range(20):
            provider.tts_text_queue.put(self.message('x' * 40))
        self.assertIsNone(provider._collect_queued_text('s'))
        self.assertEqual(240, len(''.join(provider.tts_text_buff)))
        self.assertEqual(14, provider.tts_text_queue.qsize())

    def test_text_worker_merges_arrivals_during_synthesis_and_keeps_last(self):
        provider = self.provider()
        provider.conn = NS(config={}, sentence_id='s', client_abort=False,
                           stop_event=threading.Event())
        provider.tts_text_queue.put(self.message(kind=SentenceType.FIRST, content=ContentType.ACTION))
        provider.tts_text_queue.put(self.message('这是第一段已经准备好的语音内容。'))
        spoken = []
        def synthesize(text, **kwargs):
            spoken.append(text)
            if len(spoken) == 1:
                provider.tts_text_queue.put(self.message('这是第二段已经准备好的语音内容。'))
                provider.tts_text_queue.put(self.message('这是第三段已经准备好的语音内容。'))
                provider.tts_text_queue.put(self.message(kind=SentenceType.LAST, content=ContentType.ACTION))
        provider.to_tts_stream = synthesize
        original_put = provider.tts_audio_queue.put
        def output(message):
            original_put(message)
            if message[0] == SentenceType.LAST:
                provider.conn.stop_event.set()
        provider.tts_audio_queue.put = output
        worker = threading.Thread(target=provider.tts_text_priority_thread, daemon=True)
        worker.start()
        worker.join(timeout=2)
        provider.conn.stop_event.set()
        self.assertFalse(worker.is_alive())
        self.assertEqual(2, len(spoken))
        self.assertIn('第二段', spoken[1])
        self.assertIn('第三段', spoken[1])
        self.assertEqual(SentenceType.LAST, provider.tts_audio_queue.get_nowait()[0])
        self.assertTrue(provider.tts_text_queue.empty())
