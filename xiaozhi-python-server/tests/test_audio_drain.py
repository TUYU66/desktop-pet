"""Audio sender failures must finish a reply without hardware or network calls."""
import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from core.handle.sendAudioHandle import _wait_for_audio_completion, send_tts_message, sendAudio
from core.utils.audioRateController import AudioRateController


class AudioDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_music_title_is_separate_from_lyric_and_bound_to_current_stream(self):
        conn = self.connection(None)
        await send_tts_message(conn, 'start', media='music', music_title='周杰伦 - 晴天')
        await send_tts_message(conn, 'sentence_start', '故事的小黄花', media='music')
        start, caption = [json.loads(call.args[0]) for call in conn.websocket.send.await_args_list]
        self.assertEqual(start['music_title'], '周杰伦 - 晴天')
        self.assertNotIn('text', start)
        self.assertEqual(caption['text'], '故事的小黄花')
        self.assertNotIn('music_title', caption)
        self.assertEqual(start['stream_id'], caption['stream_id'])
        conn.websocket.send.reset_mock()
        await send_tts_message(conn, 'start', media='music', stream_id='obsolete', music_title='旧歌曲')
        conn.websocket.send.assert_not_awaited()

    def connection(self, controller):
        conn = NS(audio_rate_controller=controller, logger=Mock(), sentence_id='reply',
                  session_id='session', config={}, close_after_chat=False,
                  client_is_speaking=True, websocket=NS(send=AsyncMock()))
        conn.clearSpeakStatus = lambda: setattr(conn, 'client_is_speaking', False)
        return conn

    async def asyncTearDown(self):
        controller = getattr(self, 'controller', None)
        if controller:
            controller.stop_sending()
            if controller.pending_send_task:
                await asyncio.gather(controller.pending_send_task, return_exceptions=True)

    async def test_failed_sender_unblocks_last_even_when_failed_packet_was_last(self):
        self.controller = AudioRateController(frame_duration=1)
        conn = self.connection(self.controller)
        self.controller.start_sending(AsyncMock(side_effect=ConnectionError('socket closed')))
        self.controller.add_audio(b'opus')
        with self.assertRaisesRegex(ConnectionError, '音频发送失败'):
            await asyncio.wait_for(send_tts_message(conn, 'stop'), 1)
        self.assertFalse(conn.client_is_speaking)
        self.assertFalse(self.controller.queue_empty_event.is_set())
        conn.websocket.send.assert_not_awaited()  # No false successful stop acknowledgement.

    async def test_subtitle_callback_failure_also_unblocks_drain(self):
        self.controller = AudioRateController(frame_duration=1)
        self.controller.start_sending(AsyncMock())
        self.controller.add_message(AsyncMock(side_effect=ConnectionError('subtitle failed')))
        with self.assertRaisesRegex(ConnectionError, '音频发送失败'):
            await asyncio.wait_for(_wait_for_audio_completion(self.connection(self.controller)), 1)

    async def test_unexpected_sender_cancellation_is_not_successful_delivery(self):
        self.controller = AudioRateController(frame_duration=1)
        started = asyncio.Event()
        async def blocked_send(packet):
            started.set()
            await asyncio.Event().wait()
        sender = self.controller.start_sending(blocked_send)
        self.controller.add_audio(b'opus')
        await asyncio.wait_for(started.wait(), 1)
        waiting = asyncio.create_task(_wait_for_audio_completion(self.connection(self.controller)))
        sender.cancel()
        with self.assertRaisesRegex(ConnectionError, '队列未发送完成'):
            await asyncio.wait_for(waiting, 1)

    async def test_reset_and_new_sender_do_not_inherit_previous_failure(self):
        self.controller = AudioRateController(frame_duration=1)
        old = self.controller.start_sending(AsyncMock(side_effect=OSError('lost connection')))
        self.controller.add_audio(b'old')
        await asyncio.wait_for(old, 1)
        self.controller.reset()
        send = AsyncMock()
        self.controller.start_sending(send)
        self.controller.add_audio(b'new')
        await asyncio.wait_for(_wait_for_audio_completion(self.connection(self.controller)), 1)
        send.assert_awaited_once_with(b'new')
        self.assertIsNone(self.controller.send_error)

    async def test_failed_reply_cannot_restart_sender_and_discard_unsent_packets(self):
        self.controller = AudioRateController(frame_duration=1)
        conn = self.connection(self.controller)
        conn.audio_flow_control = {'sentence_id': 'reply'}
        sender = self.controller.start_sending(AsyncMock(side_effect=OSError('socket failed')))
        self.controller.add_audio(b'failed')
        self.controller.add_audio(b'unsent')
        await asyncio.wait_for(sender, 1)
        with self.assertRaisesRegex(ConnectionError, '本轮音频发送已失败'):
            await sendAudio(conn, b'next')
        self.assertEqual([('audio', b'unsent')], list(self.controller.queue))
        self.assertIs(sender, self.controller.pending_send_task)

    async def test_cancelling_drain_does_not_cancel_shared_sender(self):
        self.controller = AudioRateController(frame_duration=1)
        started, release = asyncio.Event(), asyncio.Event()
        async def blocked_send(packet):
            started.set()
            await release.wait()
        sender = self.controller.start_sending(blocked_send)
        self.controller.add_audio(b'opus')
        await asyncio.wait_for(started.wait(), 1)
        waiting = asyncio.create_task(_wait_for_audio_completion(self.connection(self.controller)))
        await asyncio.sleep(0)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiting
        self.assertFalse(sender.done())
        release.set()
        await asyncio.wait_for(_wait_for_audio_completion(self.connection(self.controller)), 1)
