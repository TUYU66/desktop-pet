import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from core.api.chat_handler import ChatHandler


class InternalChatAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {'XIAOZHI_DEVICE_SERVICE_KEY': 'test-private'})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_anonymous_requests_cannot_send_read_or_clear(self):
        api=ChatHandler({},None)
        request=SimpleNamespace(remote='192.168.1.20',headers={},json=AsyncMock(),match_info={'requestId':'x','sessionId':'x'})
        for handle in (api.handle_send,api.handle_request_status,api.handle_clear_memory):
            with self.subTest(handle=handle.__name__):
                self.assertEqual(401,(await handle(request)).status)
        request.json.assert_not_awaited()

    async def test_private_key_allows_validation_without_device_fallback(self):
        api=ChatHandler({},None)
        request=SimpleNamespace(remote='192.168.1.20',headers={'Service-Key':'test-private'},json=AsyncMock(return_value={'text':'hello'}))
        with patch.dict('os.environ',{'XIAOZHI_DEVICE_SERVICE_KEY':'test-private'}):
            self.assertEqual(404,(await api.handle_send(request)).status)

    async def test_wrong_key_cannot_clear_memory(self):
        api=ChatHandler({},None)
        request=SimpleNamespace(remote='127.0.0.1',headers={'Service-Key':'xiaozhi-device'},match_info={'sessionId':'x'})
        with patch.dict('os.environ',{'XIAOZHI_DEVICE_SERVICE_KEY':'test-private'}):
            self.assertEqual(401,(await api.handle_clear_memory(request)).status)
