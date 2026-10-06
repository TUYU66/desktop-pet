"""Voice/standby regression cases. Run in the project's Python environment."""
import json
import asyncio
import queue
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.conversation.standby import (begin_interaction, conversation_awake, exit_requested,
                                maybe_idle_standby, request_standby, stop_speaking_requested)


class StandbyIntentTests(unittest.TestCase):
    def test_listening_control_is_distinct_from_body_rest_and_discussion(self):
        for text in ('去待命休息', '请你进入待机状态吧', '停止聆听', '再见', '小智，先回到待命休息一下吧',
                     '好了，你可以去待命了', '我们暂时不聊了', '请回到待命好吗？'):
            with self.subTest(text=text): self.assertTrue(exit_requested(text))
        for text in ('休息一下', '往后靠休息', '不要去待命', '如果去待命会怎样',
                     '你是不是去待命了', '解释“停止聆听”', '待命会断网吗', '取消提醒'):
            with self.subTest(text=text): self.assertFalse(exit_requested(text))

    def test_custom_wake_prefix_and_control_are_not_general_discussion(self):
        self.assertTrue(exit_requested('小鹿小鹿，待命一下', '小鹿小鹿'))
        self.assertTrue(stop_speaking_requested('先别说了'))
        self.assertFalse(stop_speaking_requested('解释一下停止播报'))
        self.assertFalse(exit_requested('不要待命一下'))


class IdleStandbyTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_tts_still_stops_old_output_and_enters_standby(self):
        conn = self.make_conn()
        conn.session_id = 'session'
        conn.sentence_id = 'old'
        conn.clear_queues = Mock()
        conn.clearSpeakStatus = Mock()
        async def cleanup(_):
            events = [json.loads(call.args[0]) for call in conn.websocket.send.call_args_list]
            self.assertEqual(['tts'], [event['type'] for event in events])
            self.assertTrue(events[0]['aborted'])
        with patch('core.music.pause_for_chat', side_effect=cleanup), patch('core.reminders.music.interrupt_music', new_callable=AsyncMock):
            await request_standby(conn)
        self.assertTrue(conn.standby)
        self.assertTrue(json.loads(conn.websocket.send.call_args.args[0])['force'])
        self.assertNotEqual('old', conn.sentence_id)
        conn.clear_queues.assert_called_once()

    def make_conn(self):
        return NS(features={'standby_connection': True}, standby=False, conversation_awake=True,
                  client_is_speaking=False, client_have_voice=False, tts_finishing=False,
                  chat_lock=threading.Lock(), config={'standby_idle_seconds': 30},
                  last_conversation_activity=100, last_activity_time=999999,
                  reset_audio_states=Mock(), websocket=NS(send=AsyncMock()), logger=Mock())

    def speaking_conn(self):
        conn = self.make_conn()
        conn.conversation_awake = True
        conn.session_id = 'session'
        conn.sentence_id = 'old'
        conn.client_abort = False
        conn.close_after_chat = False
        conn.return_to_standby = False
        conn.stop_event = threading.Event()
        conn.clear_queues = Mock()
        conn.clearSpeakStatus = Mock()
        conn.tts = NS(tts_text_queue=queue.Queue(), tts_audio_first_sentence=False)
        return conn

    async def queue_standby(self, conn):
        with patch('core.music.pause_for_chat', new_callable=AsyncMock), \
                patch('core.reminders.music.interrupt_music', new_callable=AsyncMock):
            await request_standby(conn)
        self.addAsyncCleanup(self.cancel_fallback, conn)

    async def cancel_fallback(self, conn):
        task = getattr(conn, '_standby_reply_task', None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_exit_confirmation_finishes_before_forced_standby(self):
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        conn = self.speaking_conn()
        await self.queue_standby(conn)
        self.assertFalse(conn.standby)
        self.assertEqual(['tts'], [json.loads(call.args[0])['type']
                                  for call in conn.websocket.send.call_args_list])
        messages = list(conn.tts.tts_text_queue.queue)
        self.assertEqual([SentenceType.FIRST, SentenceType.MIDDLE, SentenceType.LAST],
                         [message.sentence_type for message in messages])
        self.assertIn('先歇会儿', messages[1].content_detail)
        async def finish_audio(*args, **kwargs):
            self.assertFalse(conn.standby)
            self.assertEqual(conn.sentence_id, conn.standby_after_sentence)
        with patch('core.handle.sendAudioHandle.send_tts_message', side_effect=finish_audio):
            await sendAudioMessage(conn, SentenceType.LAST, [], None, conn.sentence_id)
        self.assertTrue(conn.standby)
        self.assertFalse(conversation_awake(conn))
        self.assertTrue(json.loads(conn.websocket.send.call_args.args[0])['force'])

    async def test_new_wake_while_exit_audio_drains_cancels_exit(self):
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        conn = self.speaking_conn()
        await self.queue_standby(conn)
        async def wake_during_drain(*args, **kwargs):
            begin_interaction(conn, awake=True)
        with patch('core.handle.sendAudioHandle.send_tts_message', side_effect=wake_during_drain):
            await sendAudioMessage(conn, SentenceType.LAST, [], None, conn.sentence_id)
        self.assertFalse(conn.standby)
        self.assertTrue(conversation_awake(conn))
        self.assertIsNone(conn.standby_after_sentence)
        self.assertFalse(any(json.loads(call.args[0])['type'] == 'standby'
                             for call in conn.websocket.send.call_args_list))

    async def test_exit_timeout_has_guarded_control_fallback(self):
        conn = self.speaking_conn()
        gate = asyncio.Event()
        async def deadline(_):
            await gate.wait()
        with patch('core.conversation.standby.asyncio.sleep', side_effect=deadline), \
                patch('core.music.pause_for_chat', new_callable=AsyncMock), \
                patch('core.reminders.music.interrupt_music', new_callable=AsyncMock):
            await request_standby(conn)
            gate.set()
            await conn._standby_reply_task
        self.assertTrue(conn.standby)
        self.assertTrue(json.loads(conn.websocket.send.call_args.args[0])['force'])

    async def test_music_end_listens_only_if_conversation_was_awake(self):
        from core.handle.sendAudioHandle import send_tts_message
        for awake in (False, True):
            conn = self.speaking_conn()
            conn.conversation_awake = awake
            with patch('core.handle.sendAudioHandle._wait_for_audio_completion', new_callable=AsyncMock):
                await send_tts_message(conn, 'stop', media='music')
            message = json.loads(conn.websocket.send.call_args.args[0])
            self.assertEqual(awake, message['listen_after'])

    async def test_music_control_keeps_wake_but_web_only_reply_stays_idle(self):
        from core.music.conversation import say
        for awake in (False, True):
            conn = self.speaking_conn()
            conn.conversation_awake = awake
            await say(conn, '音乐已暂停')
            self.assertEqual(not awake, conn.return_to_standby)
            self.assertEqual(awake, conversation_awake(conn))

    async def test_automatic_and_manual_listen_cannot_cancel_exit_but_detect_can(self):
        from core.handle.textHandler.listenMessageHandler import ListenTextMessageHandler
        conn = self.speaking_conn()
        await self.queue_standby(conn)
        handler = ListenTextMessageHandler()
        sentence = conn.standby_after_sentence
        await handler.handle(conn, {'state': 'start', 'mode': 'realtime'})
        self.assertEqual(sentence, conn.standby_after_sentence)
        self.assertTrue(conn.return_to_standby)
        await handler.handle(conn, {'state': 'start', 'mode': 'manual'})
        self.assertEqual(sentence, conn.standby_after_sentence)
        self.assertFalse(conversation_awake(conn))
        with patch('core.music.pause_for_chat', new_callable=AsyncMock):
            await handler.handle(conn, {'state': 'detect'})
        self.assertIsNone(conn.standby_after_sentence)
        self.assertTrue(conversation_awake(conn))
        self.assertFalse(conn.return_to_standby)

    async def test_full_duplex_start_during_web_reply_is_not_a_wake(self):
        from core.handle.textHandler.listenMessageHandler import ListenTextMessageHandler
        conn = self.speaking_conn()
        conn.conversation_awake = False
        conn.client_is_speaking = True
        conn.return_to_standby = True
        await ListenTextMessageHandler().handle(conn, {'state': 'start', 'mode': 'realtime'})
        self.assertFalse(conversation_awake(conn))
        self.assertTrue(conn.return_to_standby)

    async def test_model_cannot_end_conversation_after_music_or_schedule(self):
        from plugins_func.functions.handle_exit_intent import handle_exit_intent
        from plugins_func.register import Action
        conn = self.speaking_conn()
        for text in ('下一首', '明天中午提醒我拿快递', '提醒设置好了吗'):
            conn.latest_user_text = text
            with patch('core.conversation.standby.request_standby', new_callable=AsyncMock) as standby:
                result = await handle_exit_intent(conn, '再见')
            standby.assert_not_awaited()
            self.assertEqual(Action.REQLLM, result.action)
            self.assertTrue(conversation_awake(conn))

    async def test_transport_heartbeat_does_not_keep_conversation_awake(self):
        conn = self.make_conn()
        self.assertFalse(await maybe_idle_standby(conn, now=129))
        self.assertTrue(await maybe_idle_standby(conn, now=130))
        self.assertTrue(conn.standby)
        conn.reset_audio_states.assert_called_once()
        self.assertIn('standby', conn.websocket.send.call_args.args[0])

    async def test_idle_farewell_keeps_listening_until_playback_finishes(self):
        from core.handle.sendAudioHandle import sendAudioMessage, send_tts_message
        from core.conversation.standby import listening_allowed
        from core.providers.tts.dto.dto import SentenceType
        conn = self.speaking_conn()
        self.addAsyncCleanup(self.cancel_fallback, conn)
        self.assertTrue(await maybe_idle_standby(conn, now=130))
        self.assertTrue(conversation_awake(conn))
        self.assertTrue(listening_allowed(conn))
        self.assertFalse(conn.standby)
        self.assertFalse(getattr(conn, 'explicit_standby', False))
        self.assertIn('我先待命啦', list(conn.tts.tts_text_queue.queue)[1].content_detail)
        await send_tts_message(conn, 'start')
        self.assertTrue(json.loads(conn.websocket.send.call_args.args[0])['listen_after'])
        conn.websocket.send.reset_mock()
        with patch('core.handle.sendAudioHandle._wait_for_audio_completion', new_callable=AsyncMock):
            await sendAudioMessage(conn, SentenceType.LAST, [], None, conn.sentence_id)
        frames = [json.loads(call.args[0]) for call in conn.websocket.send.call_args_list]
        self.assertEqual(['tts', 'standby'], [frame['type'] for frame in frames])
        self.assertFalse(frames[0]['listen_after'])
        self.assertFalse(conversation_awake(conn))

    async def test_idle_farewell_is_cancelled_by_real_speech_interruption(self):
        from core.handle.abortHandle import handleAbortMessage
        from core.conversation.standby import listening_allowed
        conn = self.speaking_conn()
        self.addAsyncCleanup(self.cancel_fallback, conn)
        self.assertTrue(await maybe_idle_standby(conn, now=130))
        with patch('core.music.pause_for_chat', new_callable=AsyncMock), \
                patch('core.reminders.music.interrupt_music', new_callable=AsyncMock):
            await handleAbortMessage(conn)
        self.assertTrue(conversation_awake(conn))
        self.assertTrue(listening_allowed(conn))
        self.assertIsNone(conn.standby_after_sentence)
        self.assertFalse(conn.return_to_standby)
        self.assertTrue(json.loads(conn.websocket.send.call_args.args[0])['listen_after'])

    async def test_unwoken_web_reply_and_active_recognition_do_not_start_idle_farewell(self):
        conn = self.speaking_conn()
        conn.conversation_awake = False
        self.assertFalse(await maybe_idle_standby(conn, now=200))
        conn.conversation_awake = True
        conn._asr_worker = asyncio.create_task(asyncio.Event().wait())
        try:
            self.assertFalse(await maybe_idle_standby(conn, now=200))
        finally:
            conn._asr_worker.cancel()
            await asyncio.gather(conn._asr_worker, return_exceptions=True)
        self.assertTrue(conn.tts.tts_text_queue.empty())

    async def test_speaking_thinking_and_new_voice_are_not_idle(self):
        for flag in ('client_is_speaking', 'client_have_voice', 'tts_finishing'):
            conn = self.make_conn()
            setattr(conn, flag, True)
            self.assertFalse(await maybe_idle_standby(conn, now=200))
        conn = self.make_conn()
        with conn.chat_lock:
            self.assertFalse(await maybe_idle_standby(conn, now=200))
        conn = self.make_conn()
        conn.config['standby_idle_seconds'] = 0
        self.assertFalse(await maybe_idle_standby(conn, now=200))

    async def test_realtime_vad_stops_old_answer_before_asr_result(self):
        from core.handle.receiveAudioHandle import handleAudioMessage
        conn = self.make_conn()
        conn.vad = NS(is_vad=Mock(return_value=True))
        conn.asr = NS(receive_audio=AsyncMock())
        conn.features['voice_barge_in'] = True
        conn.client_listen_mode = 'realtime'
        conn.client_is_speaking = True
        conn.client_abort = False
        conn.just_woken_up = True
        with patch('core.handle.receiveAudioHandle.handleAbortMessage', new_callable=AsyncMock) as stop:
            await handleAudioMessage(conn, b'new-voice')
        stop.assert_awaited_once_with(conn)
        conn.asr.receive_audio.assert_awaited_once_with(conn, b'new-voice', True)

    async def test_gated_firmware_is_not_overridden_by_server_vad(self):
        from core.handle.receiveAudioHandle import handleAudioMessage
        conn = self.make_conn()
        conn.vad = NS(is_vad=Mock(return_value=True))
        conn.asr = NS(receive_audio=AsyncMock())
        conn.features.update(voice_barge_in=True, voice_barge_in_gated=True)
        conn.client_listen_mode = 'realtime'
        conn.client_is_speaking = True
        conn.client_abort = False
        with patch('core.handle.receiveAudioHandle.handleAbortMessage', new_callable=AsyncMock) as stop:
            await handleAudioMessage(conn, b'filtered-input')
        stop.assert_not_awaited()
        conn.asr.receive_audio.assert_awaited_once()


class TtsInterruptionTests(unittest.TestCase):
    def provider(self):
        from core.providers.tts.base import TTSProviderBase
        class Provider(TTSProviderBase):
            async def text_to_speak(self, text, path): return None
        provider = Provider({}, True)
        provider.conn = NS(config={}, client_abort=False, sentence_id='old', sample_rate=24000)
        provider.current_sentence_id = 'old'
        return provider

    def test_short_opening_is_merged_and_short_final_reply_is_flushed(self):
        provider = self.provider()
        provider.tts_text_buff = ['好，']
        self.assertIsNone(provider._get_segment_text())
        provider.tts_text_buff.append('明天下午两点我会准时提醒你拿快递。')
        self.assertIn('明天', provider._get_segment_text())
        short = self.provider()
        short.tts_text_buff = ['好。']
        short.to_tts_stream = Mock()
        short._process_remaining_text_stream(opus_handler=Mock())
        short.to_tts_stream.assert_called_once()
        self.assertEqual(2, short.processed_chars)

    def test_old_synthesis_cannot_resume_after_new_turn_clears_abort_flag(self):
        provider = self.provider()
        def finish_old_request(*args, **kwargs):
            provider.conn.sentence_id = 'new'
            provider.conn.client_abort = False
            return b'old-audio'
        provider._run_async_with_timeout = Mock(side_effect=finish_old_request)
        with patch('core.providers.tts.base.audio_bytes_to_data_stream') as encode:
            provider.to_tts_stream('这是旧回复。', opus_handler=Mock())
        encode.assert_not_called()
        self.assertTrue(provider.tts_audio_queue.empty())


if __name__ == '__main__':
    unittest.main()
