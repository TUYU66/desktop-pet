import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.conversation.standby import enter_standby, begin_interaction
from core.handle.textHandler.pingMessageHandler import PingMessageHandler


class StandbyTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, enabled=True):
        return SimpleNamespace(
            features={"standby_connection": enabled}, config={},
            websocket=SimpleNamespace(send=AsyncMock()), close=AsyncMock(),
            reset_audio_states=Mock(), logger=Mock(), last_activity_time=123,
            last_transport_time=0, standby=False, close_after_chat=True,
            client_is_speaking=True, return_to_standby=True,
        )

    async def test_standby_keeps_transport_and_clears_conversation_flags(self):
        conn = self.connection()
        await enter_standby(conn)
        conn.close.assert_not_called()
        self.assertTrue(conn.standby)
        self.assertFalse(conn.close_after_chat)
        self.assertFalse(conn.client_is_speaking)
        self.assertFalse(conn.return_to_standby)
        self.assertEqual(json.loads(conn.websocket.send.call_args.args[0])["type"], "standby")

    async def test_legacy_device_still_closes_after_conversation(self):
        conn = self.connection(False)
        await enter_standby(conn)
        conn.close.assert_awaited_once()
        conn.websocket.send.assert_not_called()

    async def test_persistent_heartbeat_does_not_extend_conversation(self):
        conn = self.connection()
        await PingMessageHandler().handle(conn, {"type": "ping"})
        self.assertEqual(conn.last_activity_time, 123)
        self.assertGreater(conn.last_transport_time, 0)
        self.assertEqual(json.loads(conn.websocket.send.call_args.args[0])["type"], "pong")

    async def test_legacy_ping_respects_disabled_setting(self):
        conn = self.connection(False)
        await PingMessageHandler().handle(conn, {"type": "ping"})
        conn.websocket.send.assert_not_called()

    async def test_new_interaction_clears_stale_exit_request(self):
        conn = self.connection()
        conn.standby = True
        begin_interaction(conn)
        self.assertFalse(conn.standby)
        self.assertFalse(conn.close_after_chat)
        self.assertGreater(conn.last_activity_time, 123)
