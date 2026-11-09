import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.api.ota_handler import OTAHandler
from core.handle.sendAudioHandle import _do_send_audio
from core.handle.textMessageHandlerRegistry import TextMessageHandlerRegistry


class RobotTransportTest(unittest.IsolatedAsyncioTestCase):
    def handler(self):
        config = {
            "server": {
                "auth_key": "offline-test-secret",
                "auth": {"enabled": True},
                "port": 8001,
                "http_port": 8004,
                "websocket": "ws://robot.test:8001/xiaozhi/v1/",
                "vision_explain": "http://robot.test:8004/mcp/vision/explain",
                "mqtt_gateway": "unused-old-setting",
            }
        }
        handler = OTAHandler(config)
        handler.logger = Mock()
        return handler

    async def response(self, handler):
        request = NS(
            method="POST",
            headers={"device-id": "test-device", "client-id": "test-client"},
            text=AsyncMock(
                return_value=json.dumps(
                    {
                        "board": {"type": "bread-compact-wifi-lcd"},
                        "application": {"version": "1.0.0"},
                    }
                )
            ),
        )
        with (
            patch("core.api.ota_handler.get_local_ip", return_value="127.0.0.1"),
            patch.object(handler, "_refresh_bin_cache_if_needed"),
        ):
            response = await handler.handle_post(request)
        return json.loads(response.text)

    async def test_ota_returns_authenticated_websocket_despite_stale_gateway_setting(self):
        handler = self.handler()
        result = await self.response(handler)
        self.assertEqual("ws://robot.test:8001/xiaozhi/v1/", result["websocket"]["url"])
        self.assertNotIn("mqtt", result)
        self.assertTrue(
            handler.auth.verify_token(result["websocket"]["token"], "test-client", "test-device")
        )

    async def test_firmware_download_address_remains_available_with_vision_configuration(self):
        handler = self.handler()
        handler._bin_cache["files_by_model"] = {
            "bread-compact-wifi-lcd": [("1.1.0", "robot_1.1.0.bin")]
        }
        result = await self.response(handler)
        self.assertEqual(
            {
                "version": "1.1.0",
                "url": "http://robot.test:8004/xiaozhi/ota/download/robot_1.1.0.bin",
            },
            result["firmware"],
        )

    async def test_websocket_audio_preserves_raw_opus_packets_and_order(self):
        conn = NS(websocket=NS(send=AsyncMock()), logger=Mock())
        flow = {"packet_count": 0}
        packets = [b"\xf8\xff\xfe", b"next-frame"]
        for packet in packets:
            await _do_send_audio(conn, packet, flow)
        self.assertEqual(packets, [call.args[0] for call in conn.websocket.send.await_args_list])
        self.assertEqual(2, flow["packet_count"])

    def test_current_device_messages_keep_mcp_listening_and_abort(self):
        registry = TextMessageHandlerRegistry()
        for name in ["hello", "mcp", "listen", "abort", "ping"]:
            self.assertIsNotNone(registry.get_handler(name))
        self.assertIsNone(registry.get_handler("iot"))
