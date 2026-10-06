import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from core.api.volume_handler import VolumeHandler


class VolumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_sets_and_verifies_device_volume(self):
        api = VolumeHandler(SimpleNamespace(device_handlers={"device": object()}))
        api._call = AsyncMock(side_effect=[
            '{"audio_speaker":{"volume":60}}', 'true', '{"audio_speaker":{"volume":20}}'])
        response = await api.handle(SimpleNamespace(method="PUT", json=AsyncMock(return_value={"volume": 20})))
        self.assertEqual(json.loads(response.text)["data"]["volume"], 20)
        self.assertEqual([call.args[1] for call in api._call.call_args_list],
                         ["self.get_device_status", "self.audio_speaker.set_volume", "self.get_device_status"])

    async def test_unconfirmed_write_is_not_success(self):
        api = VolumeHandler(SimpleNamespace(device_handlers={"device": object()}))
        api._call = AsyncMock(side_effect=[
            '{"audio_speaker":{"volume":60}}', 'true', '{"audio_speaker":{"volume":60}}'])
        response = await api.handle(SimpleNamespace(method="PUT", json=AsyncMock(return_value={"volume": 20})))
        self.assertEqual(response.status, 503)

    async def test_offline_does_not_send_command(self):
        api = VolumeHandler()
        api._call = AsyncMock()
        response = await api.handle(SimpleNamespace(method="GET"))
        self.assertEqual(response.status, 409)
        api._call.assert_not_called()

    async def test_rejects_invalid_volume(self):
        api = VolumeHandler()
        for value in (-1, 101, True, "20", 20.5):
            response = await api.handle(SimpleNamespace(method="PUT", json=AsyncMock(return_value={"volume": value})))
            self.assertEqual(response.status, 400)
