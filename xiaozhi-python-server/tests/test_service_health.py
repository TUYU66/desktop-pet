import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from core.device.health import ServiceHealth, connection_services
from core.handle.textHandler.pingMessageHandler import PingMessageHandler


class ServiceHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_keeps_standby_and_reports_cached_health(self):
        from core.handle.helloHandle import handleHelloMessage
        health = ServiceHealth("http://management")
        await health.check(SimpleNamespace(get=AsyncMock(side_effect=httpx.ConnectError("offline"))))
        logger = SimpleNamespace(bind=lambda **k: SimpleNamespace(debug=lambda *a: None))
        conn = SimpleNamespace(
            server=SimpleNamespace(service_health=health), features=None, logger=logger,
            welcome_msg={"type": "hello", "transport": "websocket"},
            websocket=SimpleNamespace(send=AsyncMock()),
        )
        await handleHelloMessage(conn, {"features": {"standby_connection": True, "service_status": True}})
        reply = json.loads(conn.websocket.send.call_args.args[0])
        self.assertTrue(reply["features"]["standby_connection"])
        self.assertTrue(reply["features"]["service_status"])
        self.assertEqual(reply["services"], {"java": "offline"})
        self.assertTrue(conn.standby)

    async def test_failure_recovery_and_stale_snapshot(self):
        now = [100]
        health = ServiceHealth("http://management", clock=lambda: now[0])
        self.assertEqual(health.snapshot(), {"java": "checking"})
        client = SimpleNamespace(get=AsyncMock(side_effect=httpx.ConnectError("offline")))
        await health.check(client)
        self.assertEqual(health.snapshot(), {"java": "offline"})
        client.get.side_effect = None
        client.get.return_value = httpx.Response(200, json={"status": "online"})
        await health.check(client)
        self.assertEqual(health.snapshot(), {"java": "online"})
        client.get.assert_called_with("http://management/xiaozhi/api/health")
        now[0] += 11
        self.assertEqual(health.snapshot(), {"java": "unknown"})

    async def test_http_success_alone_does_not_prove_java_health(self):
        health = ServiceHealth("http://management")
        for response in (httpx.Response(200, text="gateway"), httpx.Response(404),
                         httpx.Response(200, json={"status": "offline"})):
            await health.check(SimpleNamespace(get=AsyncMock(return_value=response)))
            self.assertEqual(health.snapshot(), {"java": "offline"})

    async def test_cancellation_is_not_swallowed(self):
        health = ServiceHealth("http://management")
        with self.assertRaises(asyncio.CancelledError):
            await health.check(SimpleNamespace(get=AsyncMock(side_effect=asyncio.CancelledError())))
        self.assertEqual(health.snapshot(), {"java": "checking"})

    async def test_heartbeat_reports_java_offline_without_entering_listening(self):
        health = ServiceHealth("http://management")
        await health.check(SimpleNamespace(get=AsyncMock(side_effect=httpx.ReadTimeout("offline"))))
        conn = SimpleNamespace(
            server=SimpleNamespace(service_health=health),
            config={}, features={"service_status": True, "standby_connection": True},
            standby=True, last_activity_time=123,
            logger=SimpleNamespace(debug=lambda *a: None, error=lambda *a: None),
            websocket=SimpleNamespace(send=AsyncMock()),
        )
        await PingMessageHandler().handle(conn, {"type": "ping"})
        reply = json.loads(conn.websocket.send.call_args.args[0])
        self.assertEqual(reply["services"], {"java": "offline"})
        self.assertTrue(conn.standby)
        self.assertEqual(conn.last_activity_time, 123)
        self.assertEqual(connection_services(SimpleNamespace()), {"java": "unknown"})
