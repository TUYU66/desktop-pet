"""Cancel a stalled reply without releasing another turn's lock or replaying tools."""
from concurrent.futures import Future
import threading
import time
import json
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock, patch
import httpx

from core.conversation.cancellation import (
    ChatCancellation, ChatTurnCancelled, chat_scope, wait_chat_future,
    cleanup_cancelled_voice,
    current_turn, without_chat_cancellation,
)
from core.providers.llm.openai.openai import LLMProvider
from core.conversation.requests import WebChatRequest


class ChatCancellationTests(unittest.TestCase):
    def test_timeout_cancels_a_queued_worker_without_double_releasing(self):
        record = WebChatRequest('queued', ('hello', 'device', ''), SimpleNamespace(), 'device')
        lock = threading.Lock()
        lock.acquire()
        queued = Future()
        def reserved_worker_done(done):
            if done.cancelled():
                lock.release()
        queued.add_done_callback(reserved_worker_done)
        record.worker_future = queued
        record.update('unknown', 'timed out')
        record.update('unknown', 'duplicate status update')
        self.assertTrue(queued.cancelled())
        self.assertFalse(lock.locked())

    def test_cancelled_worker_releases_its_own_lock_but_does_not_cancel_a_sent_tool(self):
        conn = SimpleNamespace(chat_lock=threading.Lock())
        conn.chat_lock.acquire()
        remote_tool = Future()
        cancellation = ChatCancellation(time.monotonic() + 5)
        started = threading.Event()
        finished = threading.Event()
        errors = []
        def worker():
            try:
                with chat_scope(conn, cancellation):
                    started.set()
                    wait_chat_future(remote_tool)
                errors.append('cancelled work unexpectedly completed')
            except ChatTurnCancelled:
                pass
            finally:
                conn.chat_lock.release()
                finished.set()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            cancellation.cancel()
            self.assertTrue(finished.wait(1))
            self.assertFalse(conn.chat_lock.locked())
            self.assertFalse(remote_tool.cancelled())
            self.assertFalse(remote_tool.done())
            self.assertEqual([], errors)
            # The old cancellation object cannot stop a new turn on this device.
            with chat_scope(conn) as new_turn:
                cancellation.cancel()
                new_turn.check()
        finally:
            cancellation.cancel()
            thread.join(1)

    def provider(self, stream):
        provider = object.__new__(LLMProvider)
        provider.client = Mock(timeout=httpx.Timeout(300))
        provider.client.with_options.return_value = provider.client
        provider.client.chat.completions.create.return_value = stream
        return provider

    def test_unknown_request_closes_only_its_registered_stream(self):
        record = WebChatRequest('old', ('hello', 'device', ''), SimpleNamespace(), 'device')
        stream = Mock()
        closed = threading.Event()
        stream.close.side_effect = closed.set
        provider = self.provider(stream)
        with chat_scope(record.conn, record.cancellation):
            with provider._open_stream({'stream': True}):
                record.update('unknown', 'timed out')
                self.assertTrue(closed.wait(1))
                self.assertTrue(record.cancellation.cancelled())
        options = provider.client.with_options.call_args.kwargs
        self.assertLessEqual(options['timeout'].read, 15)
        self.assertEqual(0, options['max_retries'])

    def test_cancellation_during_headers_closes_the_late_stream(self):
        cancellation = ChatCancellation(time.monotonic() + 5)
        stream = Mock()
        provider = self.provider(stream)
        def headers(**kwargs):
            cancellation.cancel()
            return stream
        provider.client.chat.completions.create.side_effect = headers
        with chat_scope(SimpleNamespace(), cancellation):
            with self.assertRaises(ChatTurnCancelled):
                with provider._open_stream({'stream': True}):
                    self.fail('cancelled stream was exposed to the caller')
        stream.close.assert_called()

    def test_stricter_timeouts_and_background_calls_are_preserved(self):
        stream = Mock()
        provider = self.provider(stream)
        provider.client.timeout = httpx.Timeout(pool=1, connect=2, write=3, read=4)
        with chat_scope(SimpleNamespace()):
            with provider._open_stream({'stream': True}):
                pass
        options = provider.client.with_options.call_args.kwargs
        self.assertEqual(4, options['timeout'].read)
        self.assertEqual(2, options['timeout'].connect)
        provider.client.with_options.reset_mock()
        with provider._open_stream({'stream': True}):
            pass
        provider.client.with_options.assert_not_called()

    def test_expired_turn_does_not_send_a_model_request(self):
        provider = self.provider(Mock())
        turn = ChatCancellation(time.monotonic() - 1)
        with self.assertRaises(ChatTurnCancelled):
            with chat_scope(SimpleNamespace(), turn):
                with provider._open_stream({'stream': True}):
                    pass
        provider.client.chat.completions.create.assert_not_called()


class VoiceTimeoutCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_memory_does_not_inherit_reply_cancellation(self):
        release = asyncio.Event()
        async def background():
            await release.wait()
            return current_turn()
        with chat_scope(SimpleNamespace()) as turn:
            with without_chat_cancellation():
                task = asyncio.create_task(background())
            self.assertIs(turn, current_turn())
        self.assertTrue(turn.cancelled())
        release.set()
        self.assertIsNone(await task)

    async def test_old_turn_cannot_clear_a_new_turn(self):
        old_turn = ChatCancellation(time.monotonic() + 5)
        old_turn.cancel()
        conn = SimpleNamespace(_active_chat_cancellation=ChatCancellation(time.monotonic() + 5),
                               clear_queues=Mock(), websocket=SimpleNamespace(send=AsyncMock()))
        await cleanup_cancelled_voice(conn, old_turn)
        conn.clear_queues.assert_not_called()
        conn.websocket.send.assert_not_awaited()

    async def test_timeout_preserves_wake_permission(self):
        for awake in (False, True):
            with self.subTest(awake=awake):
                turn = ChatCancellation(time.monotonic() + 5)
                turn.cancel()
                conn = SimpleNamespace(_active_chat_cancellation=turn, client_abort=False,
                    input_generation=7, pending_chassis_action={}, clear_queues=Mock(),
                    clearSpeakStatus=Mock(), conversation_awake=awake, session_id='session',
                    websocket=SimpleNamespace(send=AsyncMock()))
                with patch('core.conversation.standby.listening_allowed', return_value=awake):
                    await cleanup_cancelled_voice(conn, turn)
                self.assertEqual(8, conn.input_generation)
                self.assertEqual(awake, conn.conversation_awake)
                conn.clear_queues.assert_called_once()
                payload = json.loads(conn.websocket.send.call_args.args[0])
                self.assertEqual(awake, payload['listen_after'])
                self.assertTrue(payload['aborted'])


if __name__ == '__main__':
    unittest.main()
