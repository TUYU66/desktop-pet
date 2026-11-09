import asyncio
import json
import threading
import unittest
from queue import Queue
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.device.bluetooth_notice import handle_event


class BluetoothNoticeTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return NS(stop_event=threading.Event(), logger=Mock(), websocket=NS(send=AsyncMock()),
                  tts=NS(voice='selected-voice', to_tts=Mock(return_value=[b'opus'])),
                  chat_lock=threading.Lock(), client_is_speaking=False, client_abort=False,
                  session_id='session', sentence_id='old', input_generation=0,
                  dialogue=Mock(), config={}, report_enabled=True, report_tts_enable=True,
                  chat_history_conf=1, report_queue=Queue())

    def event(self, identifier=1, enabled=True, success=True):
        return dict(type='bluetooth_control', id=identifier, enabled=enabled, success=success)

    async def run_notice(self, conn, event):
        await handle_event(conn, event)
        task = conn._bluetooth_notice_task
        if task:
            await task

    def acknowledgements(self, conn):
        return [json.loads(call.args[0]) for call in conn.websocket.send.await_args_list]

    async def test_selected_tts_subtitle_history_then_ack_and_duplicate_does_not_speak(self):
        conn = self.context()
        events = []
        async def tts(connection, state, text=None, **kwargs):
            events.append(state)
            connection.client_is_speaking = state != 'stop'
        with patch('core.handle.sendAudioHandle.send_tts_message', side_effect=tts), \
                patch('core.handle.sendAudioHandle.sendAudio', new_callable=AsyncMock) as audio:
            await self.run_notice(conn, self.event())
            self.assertEqual(['start', 'sentence_start', 'stop'], events)
            conn.tts.to_tts.assert_called_once_with('App 已连接，进入蓝牙控制模式。')
            audio.assert_awaited_once_with(conn, [b'opus'])
            self.assertEqual('App 已连接，进入蓝牙控制模式。', conn.report_queue.get_nowait()[1])
            self.assertTrue(self.acknowledgements(conn)[-1]['played'])
            await handle_event(conn, self.event())
            self.assertEqual(1, conn.tts.to_tts.call_count)
            self.assertEqual(2, len(self.acknowledgements(conn)))
        self.assertFalse(conn.chat_lock.locked())

    async def test_exit_is_spoken_only_as_confirmed_exit_and_failure_is_not_success(self):
        for success, text in ((True, '已退出蓝牙控制模式。'), (False, '这次蓝牙控制没有完成确认。')):
            conn = self.context()
            with patch('core.handle.sendAudioHandle.send_tts_message', new_callable=AsyncMock), \
                    patch('core.handle.sendAudioHandle.sendAudio', new_callable=AsyncMock):
                await self.run_notice(conn, self.event(enabled=False, success=success))
            conn.tts.to_tts.assert_called_once_with(text)

    async def test_busy_conversation_does_not_get_interrupted(self):
        conn = self.context()
        conn.chat_lock.acquire()
        with patch('core.device.bluetooth_notice.SPEECH_READY_TIMEOUT', .01):
            await self.run_notice(conn, self.event())
        conn.tts.to_tts.assert_not_called()
        self.assertFalse(self.acknowledgements(conn)[-1]['played'])
        self.assertTrue(conn.chat_lock.locked())
        conn.chat_lock.release()

    async def test_voice_entry_tail_defers_notice_until_chat_and_speaker_are_free(self):
        conn = self.context()
        conn.chat_lock.acquire()
        conn.client_is_speaking = True
        conn.tts_finishing = True
        async def finish_previous():
            await asyncio.sleep(.03)
            conn.client_is_speaking = False
            conn.tts_finishing = False
            conn.chat_lock.release()
        with patch('core.handle.sendAudioHandle.send_tts_message', new_callable=AsyncMock), \
                patch('core.handle.sendAudioHandle.sendAudio', new_callable=AsyncMock):
            previous = asyncio.create_task(finish_previous())
            await self.run_notice(conn, self.event())
            await previous
        conn.tts.to_tts.assert_called_once_with('App 已连接，进入蓝牙控制模式。')
        self.assertTrue(self.acknowledgements(conn)[-1]['played'])
        self.assertFalse(conn.chat_lock.locked())

    async def test_cancel_while_waiting_never_speaks_or_releases_other_chat_lock(self):
        conn = self.context()
        conn.chat_lock.acquire()
        await handle_event(conn, self.event())
        pending = conn._bluetooth_notice_task
        await asyncio.sleep(.01)
        await handle_event(conn, dict(type='bluetooth_control_cancel', id=1))
        await pending
        conn.tts.to_tts.assert_not_called()
        self.assertFalse(self.acknowledgements(conn)[-1]['played'])
        self.assertTrue(conn.chat_lock.locked())
        conn.chat_lock.release()

    async def test_exit_supersedes_a_connection_notice_waiting_for_chat(self):
        conn = self.context()
        conn.chat_lock.acquire()
        with patch('core.handle.sendAudioHandle.send_tts_message', new_callable=AsyncMock), \
                patch('core.handle.sendAudioHandle.sendAudio', new_callable=AsyncMock):
            await handle_event(conn, self.event())
            first = conn._bluetooth_notice_task
            await asyncio.sleep(.01)
            await handle_event(conn, self.event(identifier=2, enabled=False))
            second = conn._bluetooth_notice_task
            conn.chat_lock.release()
            await asyncio.gather(first, second)
        conn.tts.to_tts.assert_called_once_with('已退出蓝牙控制模式。')
        self.assertEqual({1: False, 2: True}, {item['id']: item['played'] for item in self.acknowledgements(conn)})

    async def test_synthesis_failure_has_no_recording_fallback_or_success_ack(self):
        conn = self.context()
        conn.tts.to_tts.side_effect = TimeoutError
        with patch('core.handle.sendAudioHandle.sendAudio', new_callable=AsyncMock) as audio:
            await self.run_notice(conn, self.event())
        audio.assert_not_awaited()
        self.assertFalse(self.acknowledgements(conn)[-1]['played'])
        self.assertTrue(conn.report_queue.empty())
        self.assertFalse(conn.chat_lock.locked())

    async def test_cancel_does_not_stop_a_newer_reply(self):
        conn = self.context()
        sending = asyncio.Event()
        stop_calls = []
        async def audio(*args):
            sending.set()
            await asyncio.Event().wait()
        async def tts(connection, state, *args, **kwargs):
            stop_calls.append(state)
        with patch('core.handle.sendAudioHandle.send_tts_message', side_effect=tts), \
                patch('core.handle.sendAudioHandle.sendAudio', side_effect=audio):
            await handle_event(conn, self.event())
            task = conn._bluetooth_notice_task
            await asyncio.wait_for(sending.wait(), 2)
            conn.sentence_id = 'new-reply'
            conn.input_generation += 1
            await handle_event(conn, dict(type='bluetooth_control_cancel', id=1))
            await task
        self.assertNotIn('stop', stop_calls)
        self.assertFalse(self.acknowledgements(conn)[-1]['played'])
        self.assertFalse(conn.chat_lock.locked())

    async def test_protocol_rejects_malformed_event(self):
        conn = self.context()
        for values in (dict(id=True), dict(id=0), dict(id=65536), dict(enabled=1), dict(success='true')):
            await handle_event(conn, {**self.event(), **values})
        self.assertFalse(hasattr(conn, '_bluetooth_notice_task'))
        conn.websocket.send.assert_not_awaited()

    async def test_exit_waits_for_canceled_connection_notice_to_release_speech(self):
        conn = self.context()
        began = asyncio.Event()
        sends = 0
        async def audio(*args):
            nonlocal sends
            sends += 1
            if sends == 1:
                began.set()
                await asyncio.Event().wait()
        async def tts(connection, state, *args, **kwargs):
            if state == 'stop':
                await asyncio.sleep(.02)
            connection.client_is_speaking = state != 'stop'
        with patch('core.handle.sendAudioHandle.send_tts_message', side_effect=tts), \
                patch('core.handle.sendAudioHandle.sendAudio', side_effect=audio):
            await handle_event(conn, self.event())
            first = conn._bluetooth_notice_task
            await asyncio.wait_for(began.wait(), 2)
            await handle_event(conn, self.event(identifier=2, enabled=False))
            second = conn._bluetooth_notice_task
            await asyncio.gather(first, second)
        self.assertEqual('已退出蓝牙控制模式。', conn.tts.to_tts.call_args.args[0])
        results = {item['id']: item['played'] for item in self.acknowledgements(conn)}
        self.assertEqual({1: False, 2: True}, results)
        self.assertFalse(conn.chat_lock.locked())


if __name__ == '__main__':
    unittest.main()
