"""Voice authorization, transient posture context and web control history."""
import asyncio
import json
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.device.context import read_snapshot, render_context
from core.conversation.standby import begin_interaction, conversation_awake, listening_allowed
from core.utils.dialogue import Dialogue, Message


class VoiceStateTests(unittest.TestCase):
    def test_web_turn_preserves_voice_authorization_and_pending_exit(self):
        for awake in (False, True):
            conn = NS(features={'standby_connection': True}, conversation_awake=awake,
                      standby=True, explicit_standby=False)
            begin_interaction(conn)
            self.assertEqual(awake, listening_allowed(conn))
            self.assertEqual(awake, conversation_awake(conn))
        conn.explicit_standby = True
        conn.standby_after_sentence = 'confirmation'
        begin_interaction(conn)
        self.assertTrue(conn.explicit_standby)
        self.assertEqual('confirmation', conn.standby_after_sentence)
        self.assertFalse(listening_allowed(conn))

    def test_body_and_voice_state_are_independent(self):
        for awake in (False, True):
            for posture, expected in (('standing', '站立，'), ('seated', '坐下，')):
                conn = NS(conversation_awake=awake, chat_input_source='web')
                data = {'conversationAwake': awake, 'chassis': {
                    'connected': True, 'motionValid': True, 'busy': False,
                    'phase': 'idle', 'posture': posture}}
                text = render_context(conn, data)
                self.assertIn(expected, text)
                self.assertIn('这条消息不会唤醒机器人', text)
                self.assertIn('语音会话仍开启' if awake else '语音会话未开启', text)

    def test_completed_standby_is_not_reported_as_pending_forever(self):
        conn = NS(conversation_awake=False, explicit_standby=True, standby_after_sentence=None)
        self.assertIn('语音模式：待命，语音会话未开启', render_context(conn))
        conn.standby_after_sentence = 'confirmation'
        self.assertIn('正在结束语音会话', render_context(conn))

    def test_old_stopped_or_busy_state_is_not_a_confirmed_posture(self):
        conn = NS(conversation_awake=False)
        data = {'chassis': {'connected': True, 'motionValid': False, 'posture': 'seated'}}
        self.assertIn('身体状态：姿态未知', render_context(conn, data))
        data['chassis'].update(motionValid=True, phase='rising', busy=True, posture='standing')
        self.assertIn('身体状态：正在起身，动作尚未完成', render_context(conn, data))
        data['chassis'].update(phase='idle', busy=False, posture='unknown', statusValid=True, balanceStopped=True)
        self.assertIn('不足以确认已经坐下', render_context(conn, data))
        self.assertIn('状态待确认', render_context(conn, {'conversationAwake': True}))

    def test_runtime_state_is_replaced_per_turn_and_never_persisted(self):
        dialogue = Dialogue()
        dialogue.put(Message(role='system', content='你是小智。'))
        dialogue.put(Message(role='user', content='现在站着吗？'))
        first = dialogue.get_llm_dialogue_with_memory(runtime_context='本轮站立')
        second = dialogue.get_llm_dialogue_with_memory(runtime_context='本轮坐下')
        self.assertIn({'role': 'system', 'content': '本轮站立'}, first)
        self.assertNotIn({'role': 'system', 'content': '本轮站立'}, second)
        self.assertIn({'role': 'system', 'content': '本轮坐下'}, second)
        self.assertEqual(2, len(dialogue.dialogue))


class VoiceTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_start_and_stop_carry_same_wake_permission(self):
        from core.handle.sendAudioHandle import send_tts_message
        for awake in (False, True):
            conn = NS(features={'standby_connection': True}, conversation_awake=awake,
                      session_id='session', sentence_id='reply', config={}, client_abort=False,
                      websocket=NS(send=AsyncMock()), clearSpeakStatus=Mock(), logger=Mock())
            with patch('core.handle.sendAudioHandle._wait_for_audio_completion', new_callable=AsyncMock):
                await send_tts_message(conn, 'start')
                await send_tts_message(conn, 'stop')
            frames = [json.loads(call.args[0]) for call in conn.websocket.send.call_args_list]
            self.assertEqual([awake, awake], [frame['listen_after'] for frame in frames])

    async def test_only_detect_can_open_session(self):
        from core.handle.textHandler.listenMessageHandler import ListenTextMessageHandler
        conn = NS(features={'standby_connection': True}, conversation_awake=False,
                  explicit_standby=False, client_is_speaking=False, config={}, reset_audio_states=Mock(), logger=Mock())
        handler = ListenTextMessageHandler()
        for mode in ('manual', 'realtime', 'auto'):
            await handler.handle(conn, {'state': 'start', 'mode': mode})
            self.assertFalse(conversation_awake(conn))
        conn.reset_audio_states.assert_not_called()
        with patch('core.music.pause_for_chat', new_callable=AsyncMock):
            await handler.handle(conn, {'state': 'detect'})
        self.assertTrue(conversation_awake(conn))

    async def test_unwoken_audio_and_old_asr_cannot_open_session(self):
        from core.handle.receiveAudioHandle import handleAudioMessage, startToChat
        conn = NS(features={'standby_connection': True}, conversation_awake=False,
                  vad=NS(is_vad=Mock()), asr=NS(receive_audio=AsyncMock()), logger=Mock())
        await handleAudioMessage(conn, b'echo')
        await startToChat(conn, '回声')
        conn.vad.is_vad.assert_not_called()
        conn.asr.receive_audio.assert_not_awaited()
        self.assertFalse(conversation_awake(conn))

    async def test_snapshot_is_read_only_bounded_and_never_retried(self):
        client = NS(ready=True, name_mapping={'self_dashboard_get_state': 'self.dashboard.get_state'},
                    has_tool=lambda name: name == 'self_dashboard_get_state')
        conn = NS(mcp_client=client)
        with patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool',
                   new_callable=AsyncMock, return_value='{"conversationAwake":false}') as call:
            self.assertEqual({'conversationAwake': False}, await read_snapshot(conn))
        call.assert_awaited_once_with(conn, client, 'self_dashboard_get_state', '{}', timeout=1)
        with patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool',
                   new_callable=AsyncMock, side_effect=asyncio.TimeoutError) as call:
            self.assertIsNone(await read_snapshot(conn))
        self.assertEqual(1, call.await_count)

    async def test_web_standby_and_stop_are_saved_in_selected_session(self):
        from core.api.chat_handler import ChatHandler
        from core.conversation.requests import WebChatRequest
        for text in ('你待命吧', '先别说了'):
            conn = NS(config={}, stop_event=threading.Event(), session_id='old', sentence_id='old-reply',
                      dialogue=Dialogue(), standby=True, client_abort=False, client_is_speaking=False,
                      executor=Mock())
            async def switch(session):
                conn.session_id = session
            conn.switch_session = AsyncMock(side_effect=switch)
            with patch('core.api.chat_handler.setup_logging', return_value=Mock()):
                api = ChatHandler({}, NS(device_handlers={'device': conn}))
            record = WebChatRequest('control', (text, 'device', 'chosen'), conn, 'device')
            record.task = asyncio.current_task()
            async def standby(_):
                conn.standby = True
                conn.sentence_id = 'confirmation'
                return True
            with patch('core.conversation.standby.request_standby', side_effect=standby), \
                    patch('core.handle.abortHandle.handleAbortMessage', new_callable=AsyncMock), \
                    patch('core.handle.sendAudioHandle.send_stt_message', new_callable=AsyncMock), \
                    patch('core.handle.reportHandle.enqueue_asr_report') as report:
                await api.execute(record, text, 'chosen')
            report.assert_called_once_with(conn, text, [])
            self.assertEqual('chosen', record.session)
            self.assertEqual([text], [message.content for message in conn.dialogue.dialogue])
            self.assertEqual('server_done', record.status)
            conn.executor.submit.assert_not_called()
