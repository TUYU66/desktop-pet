"""Playback handoff regressions; no device, model, or real decoder required."""
import asyncio
import tempfile
import threading
import unittest
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.music import MusicPlayer
from core.api.chat_handler import ChatHandler
from core.conversation.requests import WebChatRequest, response_progress


class MusicHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        for name in ('one.mp3', 'two.mp3'):
            (Path(self.temp.name) / name).write_bytes(b'fixture')
        self.conn = SimpleNamespace(
            config={'plugins': {'play_music': {'music_dir': self.temp.name}}},
            logger=Mock(), stop_event=threading.Event(), chat_lock=threading.Lock())
        self.player = MusicPlayer(self.conn)

    async def asyncTearDown(self):
        await self.player.close()
        self.temp.cleanup()

    async def test_empty_queues_do_not_start_before_own_reply_finishes(self):
        # The real run must stay behind the gate, before inspecting queues,
        # acquiring the audio lock, importing Opus, or creating a decoder.
        with patch('core.music.playback.shutil.which', return_value='ffmpeg'), \
                patch('core.music.playback.asyncio.create_subprocess_exec', new_callable=AsyncMock) as decoder:
            await self.player.command('play', after_sentence='reply-one')
            await asyncio.sleep(0)
            self.player.speech_finished('older-reply')
            await asyncio.sleep(0)
            self.assertFalse(self.player.speech_ready.is_set())
            self.assertFalse(self.player.task.done())
            decoder.assert_not_called()
            self.assertEqual(self.player.state, 'loading')

    async def test_switch_replaces_reply_gate_and_cancels_old_waiter(self):
        with patch('core.music.playback.shutil.which', return_value='ffmpeg'):
            await self.player.command('play', after_sentence='reply-one')
            old_task = self.player.task
            await self.player.command('next', after_sentence='reply-two')
            self.assertTrue(old_task.done())
            self.assertEqual(self.player.track['title'], 'two')
            self.player.speech_finished('reply-one')
            self.assertFalse(self.player.speech_ready.is_set())
            self.player.speech_finished('reply-two')
            self.assertTrue(self.player.speech_ready.is_set())
            # Cancel before entering the decoder; this case checks handoff only.
            await self.player.halt()

    async def test_web_button_has_no_speech_reply_dependency(self):
        with patch('core.music.playback.shutil.which', return_value='ffmpeg'):
            await self.player.command('next')
            self.assertTrue(self.player.speech_ready.is_set())
            self.assertIsNone(self.player.awaiting_sentence)
            await self.player.halt()

    async def test_decoder_cleanup_error_releases_chat_and_speaking_state(self):
        self.player.track = self.player.library.select()
        self.conn.sample_rate = 24000
        self.conn.client_is_speaking = False
        self.conn.tts = SimpleNamespace(tts_text_queue=queue.Queue(), tts_audio_queue=queue.Queue())
        event = asyncio.Event(); event.set()
        self.conn.audio_rate_controller = SimpleNamespace(queue_empty_event=event, reset=Mock())
        entered = asyncio.Event()
        async def blocked_read(size):
            entered.set()
            await asyncio.Event().wait()
        process = SimpleNamespace(returncode=None, kill=Mock(), stdout=SimpleNamespace(readexactly=blocked_read),
            communicate=AsyncMock(side_effect=OSError('pipe closed')))
        with patch('core.music.playback.local_details', new=AsyncMock(return_value={})), \
                patch('core.music.playback.asyncio.create_subprocess_exec', new=AsyncMock(return_value=process)), \
                patch.dict('sys.modules', {'opuslib_next':SimpleNamespace(Encoder=lambda *args:Mock(), APPLICATION_AUDIO=1)}), \
                patch('core.handle.sendAudioHandle.send_tts_message', new=AsyncMock()), \
                patch('core.conversation.standby.begin_interaction'), \
                patch('core.conversation.standby.supports_standby', return_value=False):
            self.player.task = asyncio.create_task(self.player.run())
            await asyncio.wait_for(entered.wait(), 1)
            self.assertTrue(self.conn.chat_lock.locked())
            self.assertTrue(self.conn.client_is_speaking)
            await asyncio.wait_for(self.player.halt(False), 1)
        process.kill.assert_called_once()
        self.assertFalse(self.conn.chat_lock.locked())
        self.assertFalse(self.conn.client_is_speaking)
        self.assertEqual(self.player.state, 'stopped')

    async def test_stop_flag_prevents_more_audio_when_pcm_read_swallows_cancel(self):
        self.player.track = self.player.library.select()
        self.conn.sample_rate = 24000
        self.conn.client_is_speaking = False
        self.conn.tts = SimpleNamespace(tts_text_queue=queue.Queue(), tts_audio_queue=queue.Queue())
        event = asyncio.Event(); event.set()
        self.conn.audio_rate_controller = SimpleNamespace(queue_empty_event=event, reset=Mock())
        entered = asyncio.Event()
        reads = 0
        async def legacy_read(stream, size):
            nonlocal reads
            reads += 1
            if reads > 1:
                entered.set()
                try: await asyncio.Event().wait()
                except asyncio.CancelledError: pass  # Simulate completion winning the cancel race.
            return b'\0'*size
        self.player.read_frame = legacy_read
        process = SimpleNamespace(returncode=None, kill=Mock(), wait=AsyncMock(),
            stdout=SimpleNamespace(), communicate=AsyncMock(return_value=(b'', b'')))
        with patch('core.music.playback.local_details', new=AsyncMock(return_value={})), \
                patch('core.music.playback.asyncio.create_subprocess_exec', new=AsyncMock(return_value=process)), \
                patch.dict('sys.modules', {'opuslib_next':SimpleNamespace(Encoder=lambda *args:SimpleNamespace(encode=lambda *args:b'opus'), APPLICATION_AUDIO=1)}), \
                patch('core.handle.sendAudioHandle.sendAudio', new=AsyncMock()) as audio, \
                patch('core.handle.sendAudioHandle.send_tts_message', new=AsyncMock()), \
                patch('core.conversation.standby.begin_interaction'), \
                patch('core.conversation.standby.supports_standby', return_value=False):
            self.player.task = asyncio.create_task(self.player.run())
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(audio.await_count, 1)
            await asyncio.wait_for(self.player.halt(), 1)
            self.assertEqual(audio.await_count, 1)
            process.wait.assert_not_awaited()  # No EOF handling or automatic next song.
        self.assertFalse(self.conn.chat_lock.locked())
        self.assertFalse(self.conn.client_is_speaking)
        self.assertIsNone(self.player.task)

    async def test_late_music_cleanup_cannot_stop_new_exit_confirmation(self):
        self.player.track = self.player.library.select()
        self.conn.sample_rate = 24000
        self.conn.client_is_speaking = False
        self.conn.tts = SimpleNamespace(tts_text_queue=queue.Queue(), tts_audio_queue=queue.Queue())
        event = asyncio.Event(); event.set()
        self.conn.audio_rate_controller = SimpleNamespace(queue_empty_event=event, reset=Mock())
        entered = asyncio.Event()
        async def blocked_read(size):
            entered.set()
            await asyncio.Event().wait()
        process = SimpleNamespace(returncode=None, kill=Mock(),
            stdout=SimpleNamespace(readexactly=blocked_read),
            communicate=AsyncMock(return_value=(b'', b'')))
        with patch('core.music.playback.local_details', new=AsyncMock(return_value={})), \
                patch('core.music.playback.asyncio.create_subprocess_exec', new=AsyncMock(return_value=process)), \
                patch.dict('sys.modules', {'opuslib_next':SimpleNamespace(Encoder=lambda *args:Mock(), APPLICATION_AUDIO=1)}), \
                patch('core.handle.sendAudioHandle.send_tts_message', new=AsyncMock()) as tts, \
                patch('core.conversation.standby.begin_interaction'):
            self.player.task = asyncio.create_task(self.player.run())
            await asyncio.wait_for(entered.wait(), 1)
            self.conn.sentence_id = 'exit-confirmation'
            self.conn.client_is_speaking = True
            self.conn.audio_rate_controller.reset.reset_mock()
            await asyncio.wait_for(self.player.halt(for_chat=True), 1)
            self.assertFalse(any(call.args[1] == 'stop' for call in tts.call_args_list))
        self.assertTrue(self.conn.client_is_speaking)
        self.conn.audio_rate_controller.reset.assert_not_called()
        self.assertFalse(self.conn.chat_lock.locked())

    async def test_pcm_read_completion_and_cancel_in_same_turn_still_cancel_reader(self):
        entered = asyncio.Event(); release = asyncio.Event()
        async def read(size):
            entered.set()
            await release.wait()
            return b'\0'*size
        operation = asyncio.create_task(self.player.read_frame(SimpleNamespace(readexactly=read), 2))
        await entered.wait()
        release.set()
        operation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await operation

    async def test_halt_retries_when_task_ignores_first_cancel(self):
        entered = asyncio.Event()
        async def stubborn_once():
            entered.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError: await asyncio.Event().wait()
        task = self.player.task = asyncio.create_task(stubborn_once())
        await entered.wait()
        wait = asyncio.wait
        with patch('core.music.playback.asyncio.wait', side_effect=lambda tasks, timeout:wait(tasks, timeout=.01)):
            await asyncio.wait_for(self.player.halt(), 1)
        self.assertTrue(task.done())
        self.assertIsNone(self.player.task)

    async def test_halt_timeout_keeps_task_reference_for_stop_retry(self):
        entered = asyncio.Event(); release = asyncio.Event()
        async def stubborn():
            entered.set()
            while not release.is_set():
                try: await release.wait()
                except asyncio.CancelledError: pass
        task = self.player.task = asyncio.create_task(stubborn())
        await entered.wait()
        wait = asyncio.wait
        try:
            with patch('core.music.playback.asyncio.wait', side_effect=lambda tasks, timeout:wait(tasks, timeout=.01)):
                with self.assertRaisesRegex(ValueError, '暂停尚未完成'):
                    await asyncio.wait_for(self.player.halt(), 1)
            self.assertIs(self.player.task, task)
            self.assertFalse(task.done())
            self.assertTrue(self.player.playback_stop.is_set())
        finally:
            release.set()
            await task
        await self.player.halt(False)
        self.assertIsNone(self.player.task)

    async def test_online_button_next_then_web_next_finishes_in_requested_session(self):
        self.conn.local_music = self.player
        self.conn.session_id = 'device-session'
        self.conn.sentence_id = 'initial'
        self.conn.client_is_speaking = False
        self.conn.client_abort = False
        self.conn.standby = False
        self.conn.close_after_chat = False
        self.conn.features = dict(standby_connection=True)
        self.conn.sample_rate = 24000
        self.conn.tts = SimpleNamespace(tts_text_queue=queue.Queue(), tts_audio_queue=queue.Queue())
        self.conn.websocket = SimpleNamespace(send=AsyncMock())
        ready = asyncio.Event(); ready.set()
        self.conn.audio_rate_controller = SimpleNamespace(queue_empty_event=ready, reset=Mock())
        async def switch(session): self.conn.session_id = session
        self.conn.switch_session = AsyncMock(side_effect=switch)
        tracks = {f'netease:{n}':dict(id=f'netease:{n}', name=f'歌曲{n}', title=f'歌曲{n}', artist='歌手', durationMs=60000) for n in (1,2,3)}
        client = Mock(generation=7)
        client.track = AsyncMock(side_effect=lambda key:dict(tracks[key]))
        client.playlist = AsyncMock(return_value=dict(name='歌单', ids=['1','2','3']))
        client.lyrics = AsyncMock(return_value={})
        client.play_url = AsyncMock(return_value='https://example.invalid/audio')
        started = [asyncio.Event(), asyncio.Event()]
        packets = 0
        async def send_audio(conn, packet):
            nonlocal packets
            if packets < len(started): started[packets].set()
            packets += 1
        def decoder(*args, **kwargs):
            frames = 0
            async def read(size):
                nonlocal frames
                frames += 1
                if frames > 1: await asyncio.Event().wait()
                return b'\0'*size
            return SimpleNamespace(returncode=None, kill=Mock(), stdout=SimpleNamespace(readexactly=read),
                communicate=AsyncMock(return_value=(b'', b'')))
        async def say(conn, text, standby=True, sentence_id=None):
            conn.client_is_speaking = False
            response_progress(conn, sentence_id, finished=True)
            self.player.speech_finished(sentence_id)
        with patch('core.api.chat_handler.setup_logging', return_value=Mock()):
            api = ChatHandler({}, SimpleNamespace(device_handlers={'device':self.conn}))
        record = WebChatRequest('next', ('下一首','device','web-session'), self.conn, 'device')
        record.task = asyncio.current_task()
        with patch('core.music.playback.netease', return_value=client), \
                patch('core.music.playback.shutil.which', return_value='ffmpeg'), \
                patch('core.music.playback.asyncio.create_subprocess_exec', new=AsyncMock(side_effect=decoder)), \
                patch.dict('sys.modules', {'opuslib_next':SimpleNamespace(Encoder=lambda *args:SimpleNamespace(encode=lambda *args:b'opus'), APPLICATION_AUDIO=1)}), \
                patch('core.handle.sendAudioHandle.sendAudio', new=AsyncMock(side_effect=send_audio)), \
                patch('core.handle.sendAudioHandle.send_tts_message', new=AsyncMock()), \
                patch('core.conversation.standby.enter_standby', new=AsyncMock()), \
                patch('core.music.conversation.say', new=AsyncMock(side_effect=say)) as reply, \
                patch('core.handle.reportHandle.enqueue_asr_report', Mock()):
            await self.player.command('play', source='netease', track_id='netease:1', playlist_id='90')
            await asyncio.wait_for(started[0].wait(), 1)
            await self.player.command('next', source='netease')
            await asyncio.wait_for(started[1].wait(), 1)
            self.assertEqual(self.player.track['id'], 'netease:2')
            await asyncio.wait_for(api.execute(record, '下一首', 'web-session'), 1)
            self.assertEqual(self.player.track['id'], 'netease:3')
            self.assertEqual(record.status, 'server_done')
            self.assertEqual(record.session, 'web-session')
            self.conn.switch_session.assert_awaited_once_with('web-session')
            self.assertIn('歌曲3', reply.call_args.args[1])
            self.assertIsNone(getattr(self.conn, 'web_chat_tracking', None))
            await self.player.halt(False)
        self.assertFalse(self.conn.chat_lock.locked())
