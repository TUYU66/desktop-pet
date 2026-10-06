"""Catch-up notices: isolate backend/TTS/audio and exercise acknowledgement ordering."""
import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch
from core.reminders.followup import deliver, notice_text, queue_on_wake, stage
from core.utils.dialogue import Dialogue
from core.conversation.requests import WebChatRequest
from test_reminder_handler import connection


class CatchupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = connection()
        self.conn.headers = {'device-id': 'target'}
        self.conn.dialogue = Dialogue()
        self.conn.standby = False
        self.conn.conversation_awake = True
        self.conn.task_followup_sentence = self.conn.sentence_id
        self.item = dict(id='reminder', title='拿快递', status='expired', updatedAt=1000,
                         scheduledAt=1790827200000, dueAt=1790830800000)
        self.events = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fetch = self.stack.enter_context(patch('core.reminders.client.catchup', new=AsyncMock(
            return_value={'items': [self.item], 'count': 2})))
        async def command(device, body):
            self.assertEqual('target', device)
            self.events.append(body['action'])
            return {'claimed': True, 'valid': True, 'finished': True}
        self.command = self.stack.enter_context(patch('core.reminders.client.command', new=AsyncMock(side_effect=command)))
        async def audio(*args): self.events.append('audio')
        async def drain(*args): self.events.append('drain')
        async def tts(conn, state, *args, **kwargs):
            self.events.append(state)
            if state == 'start': conn.client_is_speaking = True
            if state == 'stop': conn.client_is_speaking = False
        self.audio = self.stack.enter_context(patch('core.handle.sendAudioHandle.sendAudio', new=AsyncMock(side_effect=audio)))
        self.drain = self.stack.enter_context(patch('core.handle.sendAudioHandle._wait_for_audio_completion', new=AsyncMock(side_effect=drain)))
        self.tts = self.stack.enter_context(patch('core.handle.sendAudioHandle.send_tts_message', new=AsyncMock(side_effect=tts)))
        self.report = self.stack.enter_context(patch('core.handle.reportHandle.enqueue_tts_report'))

    async def test_commit_only_after_audio_drain_and_do_not_repeat_same_interaction(self):
        self.assertTrue(await deliver(self.conn, 'old'))
        self.assertEqual(['drain', 'followup_claim', 'followup_valid', 'sentence_start',
                          'audio', 'drain', 'followup_finish'], self.events)
        self.assertIn('逾期', self.report.call_args.args[1])
        self.assertIn('另外还有1条', self.report.call_args.args[1])
        self.assertFalse(await deliver(self.conn, 'old'))
        self.fetch.assert_awaited_once()

    async def test_empty_audio_releases_claim_without_consuming_notice(self):
        self.conn.tts.to_tts.return_value = []
        self.assertFalse(await deliver(self.conn, 'old'))
        self.assertEqual(['drain', 'followup_claim', 'followup_release'], self.events)
        self.audio.assert_not_awaited()
        self.report.assert_not_called()

    async def test_cancelled_or_rescheduled_reminder_not_announced(self):
        self.command.side_effect = [{'claimed': True}, {'valid': False}, {'released': True}]
        self.assertFalse(await deliver(self.conn, 'old'))
        self.audio.assert_not_awaited()
        self.assertEqual('followup_release', self.command.call_args.args[1]['action'])

    async def test_duplicate_claim_does_not_generate_or_announce_audio(self):
        self.command.return_value = {'claimed': False}
        self.command.side_effect = None
        self.assertFalse(await deliver(self.conn, 'old'))
        self.conn.tts.to_tts.assert_not_called()
        self.audio.assert_not_awaited()
        self.command.assert_awaited_once()

    async def test_barge_in_cancels_synthesis_and_releases_notice_for_next_wake(self):
        started = asyncio.Event()
        async def synthesis(*args):
            started.set()
            await asyncio.Event().wait()
        with patch('core.reminders.followup.asyncio.to_thread', new=AsyncMock(side_effect=synthesis)):
            task = asyncio.create_task(deliver(self.conn, 'old'))
            await asyncio.wait_for(started.wait(), 1)
            self.conn.client_abort = True
            self.conn.task_followup_audio_task.cancel()
            self.assertFalse(await asyncio.wait_for(task, 1))
        self.audio.assert_not_awaited()
        self.assertIn('followup_release', self.events)
        self.assertNotIn('followup_finish', self.events)

    async def test_new_user_input_during_synthesis_drops_old_notice_and_releases(self):
        def synthesis(text):
            self.conn.input_generation = 1
            return [b'opus']
        self.conn.tts.to_tts.side_effect = synthesis
        self.assertFalse(await deliver(self.conn, 'old'))
        self.audio.assert_not_awaited()
        self.assertIn('followup_release', self.events)
        self.assertNotIn('followup_finish', self.events)

    async def test_text_only_music_interaction_does_not_pause_or_send_audio(self):
        self.assertTrue(await deliver(self.conn, 'old', text_only=True))
        self.audio.assert_not_awaited()
        self.drain.assert_not_awaited()
        self.conn.tts.to_tts.assert_not_called()
        self.report.assert_called_once()
        self.assertEqual('followup_finish', self.events[-1])

    async def test_action_outcome_defers_notice_and_active_question_skips_it(self):
        self.conn.pending_chassis_action = {'sentence_id': 'old'}
        self.assertFalse(await deliver(self.conn, 'old'))
        self.assertEqual('old', self.conn.task_followup_sentence)
        self.fetch.assert_not_awaited()
        self.conn.pending_chassis_action = None
        self.conn.schedule_pending = {'choices': ['reminder']}
        self.assertFalse(await deliver(self.conn, 'old'))
        self.fetch.assert_not_awaited()

    async def test_greeting_disabled_wake_announces_and_releases_lock_without_standby(self):
        queue_on_wake(self.conn)
        await self.conn.task_followup_wake_task
        self.assertEqual('start', self.events[3])
        self.assertEqual('stop', self.events[-1])
        self.assertFalse(self.conn.chat_lock.locked())
        self.assertTrue(self.conn.conversation_awake)
        self.assertFalse(self.conn.standby)

    async def test_backend_failure_does_not_break_original_reply(self):
        self.fetch.side_effect = TimeoutError()
        self.assertFalse(await deliver(self.conn, 'old'))
        self.audio.assert_not_awaited()
        self.command.assert_not_awaited()

    async def test_web_notice_is_visible_before_synthesis_and_survives_abort(self):
        self.conn.chat_input_source = 'web'
        record = WebChatRequest('web', ('hi', 'target', ''), self.conn, 'target')
        record.session = self.conn.session_id
        record.sentence = self.conn.sentence_id
        self.conn.web_chat_tracking = record
        started = asyncio.Event()
        async def synthesis(*args):
            started.set()
            await asyncio.Event().wait()
        with patch('core.reminders.followup.asyncio.to_thread', new=AsyncMock(side_effect=synthesis)):
            task = asyncio.create_task(deliver(self.conn, 'old'))
            await asyncio.wait_for(started.wait(), 1)
            self.report.assert_called_once()
            self.assertTrue(record.optional_followup)
            self.assertEqual(record.status, 'responding')  # Web input stays guarded during speech.
            self.assertEqual([item['id'] for item in self.conn.reminder_reply_context['items']], ['reminder'])
            self.conn.client_abort = True
            self.conn.task_followup_task.cancel()
            self.assertFalse(await asyncio.wait_for(task, 1))
        self.assertIsNone(self.conn.task_followup_task)
        self.assertIn('followup_release', self.events)
        self.assertNotIn('followup_finish', self.events)
        self.audio.assert_not_awaited()

    async def test_unconfirmed_notice_can_be_reported_on_another_interaction(self):
        self.assertTrue(await deliver(self.conn, 'old'))
        self.conn.sentence_id = 'new'
        self.conn.task_followup_sentence = 'new'
        self.conn.input_generation = 1
        self.assertTrue(await deliver(self.conn, 'new'))
        self.assertEqual(self.fetch.await_count, 2)
        self.assertEqual(self.report.call_count, 2)

    def test_status_wording_and_stage_do_not_confuse_completion_with_expiry(self):
        for status, phrase in [('expired', '逾期'), ('missed', '叫过你几次'),
                               ('retry_pending', '还没听你说收到'), ('delivery_unknown', '有没有播到，我还没确认')]:
            item = dict(self.item, status=status, nextAttemptAt=2000)
            self.assertIn(phrase, notice_text(item))
            self.assertNotIn('已完成', notice_text(item))
            self.assertGreater(stage(item), 0)
        self.assertEqual(0, stage(dict(self.item, status='completed')))
        self.assertEqual(0, stage(dict(self.item, status='cancelled')))
        # Automatic retries change dueAt; the announcement retains the original appointment.
        self.assertEqual(notice_text(self.item), notice_text(dict(self.item, dueAt=1790838000000)))
