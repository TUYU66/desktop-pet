"""Missing internal credentials fail closed before any body or action is handled."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from core.api.internal_auth import internal_auth
from core.api.device_panel_handler import DevicePanelHandler
from core.api.music_handler import MusicHandler
from core.api.reminder_handler import ReminderHandler
from core.utils.service_auth import device_service_authorized


class ServiceAuthTests(unittest.IsolatedAsyncioTestCase):
    def checks(self):
        panel = object.__new__(DevicePanelHandler)
        return (
            ('XIAOZHI_DEVICE_SERVICE_KEY', 'xiaozhi-device', device_service_authorized),
            ('XIAOZHI_DEVICE_SERVICE_KEY', 'xiaozhi-device', panel.authorized),
            ('XIAOZHI_MUSIC_SERVICE_KEY', 'xiaozhi-music', MusicHandler.authorized),
            ('XIAOZHI_REMINDER_SERVICE_KEY', 'xiaozhi-reminders', ReminderHandler.authorized),
        )

    async def test_missing_and_blank_keys_never_enable_compatibility_passwords(self):
        for variable, legacy, check in self.checks():
            for configured in ('', '   '):
                for address in ('127.0.0.1', '::1', '::ffff:127.0.0.1', '192.168.1.20'):
                    with self.subTest(variable=variable, address=address, configured=configured), \
                            patch.dict('os.environ', {variable: configured}):
                        with self.assertRaises(web.HTTPServiceUnavailable) as error:
                            check(NS(remote=address, headers={'Service-Key': legacy}))
                        self.assertEqual(503, error.exception.status)
                        self.assertEqual(503, json.loads(error.exception.text)['code'])

    async def test_configured_key_accepts_only_matching_header(self):
        for variable, legacy, check in self.checks():
            with self.subTest(variable=variable), patch.dict('os.environ', {variable: 'private-test-key'}):
                for supplied in ('', legacy, 'wrong', '非ASCII'):
                    self.assertFalse(check(NS(headers={'Service-Key': supplied})))
                self.assertTrue(check(NS(headers={'Service-Key': 'private-test-key'})))

    async def test_transport_returns_503_without_running_protected_handler(self):
        handler = AsyncMock(return_value=web.json_response({'code': 0}))
        app = web.Application(middlewares=[internal_auth])
        app.router.add_post('/xiaozhi/chat/send', handler)
        async with TestClient(TestServer(app)) as client:
            with patch.dict('os.environ', {'XIAOZHI_DEVICE_SERVICE_KEY': ''}):
                response = await client.post('/xiaozhi/chat/send', data='not-json', headers={'Service-Key': 'xiaozhi-device'})
                self.assertEqual(503, response.status)
                self.assertEqual(503, (await response.json())['code'])
            handler.assert_not_awaited()

    async def test_transport_returns_401_for_wrong_key(self):
        handler = AsyncMock(return_value=web.json_response({'code': 0}))
        app = web.Application(middlewares=[internal_auth])
        app.router.add_post('/xiaozhi/chat/send', handler)
        async with TestClient(TestServer(app)) as client:
            with patch.dict('os.environ', {'XIAOZHI_DEVICE_SERVICE_KEY': 'private-test-key'}):
                response = await client.post('/xiaozhi/chat/send', headers={'Service-Key': 'wrong'})
                self.assertEqual(401, response.status)
            handler.assert_not_awaited()
