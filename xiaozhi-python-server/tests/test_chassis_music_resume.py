"""Posture/music handoff checks without a device, network, or real decoder."""
import asyncio
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.handle.sendAudioHandle import sendAudioMessage, _confirm_chassis_for_music
from core.music import MusicPlayer
from core.providers.tts.dto.dto import SentenceType
from core.conversation.requests import WebChatRequest


class ChassisMusicResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        (Path(self.temp.name) / '歌手-歌曲.mp3').write_bytes(b'fixture')
        self.conn = NS(config={'plugins': {'play_music': {'music_dir': self.temp.name}}},
            session_id='session', sentence_id='reply', logger=Mock(),
            stop_event=threading.Event(), chat_lock=threading.Lock(), client_abort=False,
            close_after_chat=False, return_to_standby=True, client_is_speaking=True,
            tts=NS(tts_audio_first_sentence=False, tts_text_queue=queue.Queue(),
                   tts_audio_queue=queue.Queue(), store_tts_text=Mock()),
            dialogue=NS(put=Mock()), features={'standby_connection': True},
            func_handler=NS(tool_manager=NS(execute_tool=AsyncMock(return_value=NS(result='起立完成。', response='')))))
        self.music = MusicPlayer(self.conn)
        self.conn.local_music = self.music
        self.music.track = self.music.library.tracks()[0]
        self.music.position = 42.5
        self.music.state = 'paused'
        self.conn.pending_chassis_action = dict(sentence_id='reply', name='self_chassis_stand_up',
            arguments={}, audio_sent=True, play_music_after=True, music_action='resume',
            music_target=('local', self.music.track['id']))
        self.record = WebChatRequest('request', ('text', '', ''), self.conn, 'device')
        self.record.session = 'session'; self.record.sentence = 'reply'
        self.conn.web_chat_tracking = self.record

    async def asyncTearDown(self):
        await self.music.close()
        self.temp.cleanup()

    async def finish_initial_reply(self, confirmation=None):
        async def tts_state(conn, state, *args, **kwargs):
            if state == 'stop': conn.client_is_speaking = False
        with patch('core.handle.sendAudioHandle.sendAudio', new_callable=AsyncMock), \
                patch('core.handle.sendAudioHandle.send_tts_message', side_effect=tts_state), \
                patch('core.reminders.followup.deliver', new_callable=AsyncMock), \
                patch('core.handle.reportHandle.enqueue_tool_report', Mock()), \
                patch('core.handle.sendAudioHandle._confirm_chassis_for_music', new=confirmation or AsyncMock(return_value=True)), \
                patch('core.conversation.standby.enter_standby', new_callable=AsyncMock), \
                patch('core.music.playback.shutil.which', return_value='ffmpeg'):
            await sendAudioMessage(self.conn, SentenceType.LAST, [], None, 'reply')

    async def test_resume_keeps_position_and_waits_until_outcome_speech_finishes(self):
        await self.finish_initial_reply()
        self.assertEqual(self.music.position, 42.5)
        self.assertEqual(self.music.track['title'], '歌曲')
        self.assertEqual(self.music.awaiting_sentence, 'reply')
        self.assertFalse(self.music.speech_ready.is_set())
        self.assertFalse(self.record.reply_finished.is_set())
        self.assertFalse(self.conn.tts_finishing)
        self.assertIsNone(self.conn.pending_chassis_action)
        messages = list(self.conn.tts.tts_text_queue.queue)
        self.assertEqual(messages[-1].sentence_type, SentenceType.LAST)
        with patch('core.handle.sendAudioHandle.sendAudio', new_callable=AsyncMock), \
                patch('core.handle.sendAudioHandle.send_tts_message', new_callable=AsyncMock), \
                patch('core.reminders.followup.deliver', new_callable=AsyncMock), \
                patch('core.conversation.standby.enter_standby', new_callable=AsyncMock):
            await sendAudioMessage(self.conn, SentenceType.LAST, [], None, 'reply')
        self.assertTrue(self.music.speech_ready.is_set())
        self.assertEqual(self.record.status, 'server_done')

    async def test_rejected_posture_does_not_resume_and_still_queues_final_feedback(self):
        self.conn.func_handler.tool_manager.execute_tool.return_value.result = '起立失败。'
        confirm = AsyncMock(return_value=True)
        await self.finish_initial_reply(confirm)
        confirm.assert_not_awaited()
        self.assertIsNone(self.music.task)
        self.assertFalse(self.conn.tts_finishing)
        self.assertEqual(list(self.conn.tts.tts_text_queue.queue)[-1].sentence_type, SentenceType.LAST)

    async def test_online_resume_keeps_same_song_queue_and_position(self):
        track = dict(id='netease:1', name='歌手 - 歌曲', title='歌曲', artist='歌手', durationMs=200000)
        self.music.source = 'netease'; self.music.track = track
        self.music.online_generation = 7; self.music.online_queue = ['netease:1', 'netease:2']
        self.conn.pending_chassis_action['music_target'] = ('netease', 'netease:1')
        client = Mock(generation=7, track=AsyncMock(return_value=track))
        with patch('core.music.playback.netease', return_value=client):
            await self.finish_initial_reply()
        self.assertEqual(self.music.track['id'], 'netease:1')
        self.assertEqual(self.music.position, 42.5)
        self.assertEqual(self.music.online_queue, ['netease:1', 'netease:2'])
        self.assertFalse(self.music.speech_ready.is_set())

    async def test_spoken_rephrasing_keeps_raw_unknown_receipt_and_blocks_music(self):
        self.conn.func_handler.tool_manager.execute_tool.return_value = NS(
            result='我没收到转向完成确认，请先看看我的状态，别连续重试。',
            response='这次有没有做好，我还没确认，你先看一下我的状态。')
        confirmation = AsyncMock(return_value=True)
        await self.finish_initial_reply(confirmation)
        confirmation.assert_not_awaited()
        self.assertTrue(self.record.action_unconfirmed)
        self.assertIsNone(self.music.task)
        text = list(self.conn.tts.tts_text_queue.queue)[1].content_detail
        self.assertIn('我还没确认',text)
        self.assertIn('音乐也还没开始放',text)
        self.assertNotIn('转向完成确认',text)

    async def test_status_timeout_leaves_music_paused_and_finishes_with_feedback(self):
        await self.finish_initial_reply(AsyncMock(side_effect=TimeoutError()))
        self.assertEqual(self.music.state, 'paused')
        self.assertIsNone(self.music.task)
        self.assertFalse(self.conn.tts_finishing)
        self.assertEqual(list(self.conn.tts.tts_text_queue.queue)[-1].sentence_type, SentenceType.LAST)

    async def test_changed_song_is_not_resumed_by_old_action(self):
        self.conn.pending_chassis_action['music_target'] = ('local', 'old-track')
        await self.finish_initial_reply()
        self.assertIsNone(self.music.task)
        text = list(self.conn.tts.tts_text_queue.queue)[1].content_detail
        self.assertIn('歌曲已变化', text)

    async def test_resume_without_a_previous_song_does_not_start_random_music(self):
        self.conn.pending_chassis_action['music_target'] = None
        await self.finish_initial_reply()
        self.assertIsNone(self.music.task)
        self.assertIn('还没有能接着放', list(self.conn.tts.tts_text_queue.queue)[1].content_detail)

    async def test_abort_during_last_status_read_does_not_resume(self):
        pending = self.conn.pending_chassis_action
        reads = []
        async def status(*args):
            reads.append(1)
            if len(reads) == 3: self.conn.client_abort = True
            return json.dumps(dict(connected=True, statusValid=True, balanceStopped=False, lowBattery=False))
        with patch('core.api.volume_handler.VolumeHandler._call', side_effect=status), \
                patch('core.handle.sendAudioHandle.asyncio.sleep', new_callable=AsyncMock):
            self.assertFalse(await _confirm_chassis_for_music(self.conn, pending))

    async def test_status_checks_share_one_deadline(self):
        real_wait_for = asyncio.wait_for
        cancelled = []
        async def stuck(*args):
            try: await asyncio.Event().wait()
            finally: cancelled.append(True)
        with patch('core.api.volume_handler.VolumeHandler._call', side_effect=stuck), \
                patch('core.handle.sendAudioHandle.asyncio.sleep', new_callable=AsyncMock), \
                patch('core.handle.sendAudioHandle.asyncio.wait_for', side_effect=lambda operation, timeout: real_wait_for(operation, timeout=.01)) as deadline:
            with self.assertRaises(asyncio.TimeoutError):
                await _confirm_chassis_for_music(self.conn, self.conn.pending_chassis_action)
        self.assertEqual(deadline.call_count, 1)
        self.assertEqual(deadline.call_args.kwargs['timeout'], 6)
        self.assertEqual(cancelled, [True])

    async def test_stuck_action_has_a_deadline_and_queues_unconfirmed_feedback(self):
        real_wait_for = asyncio.wait_for
        cancelled = []
        async def stuck(*args):
            try: await asyncio.Event().wait()
            finally: cancelled.append(True)
        self.conn.func_handler.tool_manager.execute_tool.side_effect = stuck
        with patch('core.handle.sendAudioHandle.asyncio.wait_for',
                   side_effect=lambda operation, timeout: real_wait_for(operation, .01 if timeout == 35 else timeout)):
            await self.finish_initial_reply()
        self.assertEqual(cancelled, [True])
        self.assertTrue(self.record.action_unconfirmed)
        self.assertEqual(self.record.status, 'responding')
        self.assertFalse(self.record.reply_finished.is_set())
        self.assertEqual(list(self.conn.tts.tts_text_queue.queue)[-1].sentence_type, SentenceType.LAST)


if __name__ == '__main__': unittest.main()
