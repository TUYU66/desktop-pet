import json
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
from core.api.esp32_ota_handler import Esp32OtaHandler


class Esp32OtaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {'XIAOZHI_DEVICE_SERVICE_KEY': 'test-private'})
        environment.start(); self.addCleanup(environment.stop)
        self.conn = NS(stop_event=threading.Event(), mcp_client=object())
        self.server = NS(device_handlers={'robot': self.conn})
        self.handler = Esp32OtaHandler(self.server)
        self.version = '2.2.9'
        self.chassis_active = False
        self.sent = []

        async def call(conn, name, args):
            self.sent.append((conn, name, args))
            if name == 'self.get_system_info':
                return {'application': {'version': self.version}, 'mac_address': 'robot-mac',
                        'board': {'type': 'bread-compact-wifi-lcd'}}
            if name == 'self.chassis.ota_state':
                return {'active': self.chassis_active, 'downloadOrigin': 'http://robot-server:8000'}
            return True
        self.handler.call = AsyncMock(side_effect=call)

    def request(self, body=None, auth=True):
        return NS(method='GET' if body is None else 'POST',
                  headers={'Service-Key': 'test-private'} if auth else {}, json=AsyncMock(return_value=body))

    async def start_body(self):
        state = json.loads((await self.handler.handle(self.request())).body)['data']
        return {'jobId': 'operation-1', 'connectionId': state['connectionId'], 'version': '2.2.10',
                'boardType': 'bread-compact-wifi-lcd',
                'url': 'http://robot-server:8000/xiaozhi/ota/download/' + 'a'*32}

    async def test_auth_precedes_device_and_payload_access(self):
        self.assertEqual((await self.handler.handle(self.request({}, False))).status, 401)
        self.handler.call.assert_not_awaited()

    async def test_acceptance_is_pending_until_real_new_version_is_observed(self):
        body = await self.start_body()
        response = await self.handler.handle(self.request(body))
        self.assertEqual(json.loads(response.body)['data']['phase'], 'requested')
        self.assertEqual((await self.handler.state())['phase'], 'requested')
        self.version = '2.2.10'
        state = await self.handler.state()
        self.assertEqual(state['phase'], 'completed'); self.assertFalse(state['active'])

    async def test_duplicate_requests_never_dispatch_twice(self):
        body = await self.start_body()
        await self.handler.handle(self.request(body)); await self.handler.handle(self.request(body))
        self.assertEqual(sum(name == 'self.upgrade_firmware' for _, name, _ in self.sent), 1)

    async def test_changed_connection_requires_new_confirmation(self):
        body = await self.start_body()
        self.server.device_handlers['robot'] = NS(stop_event=threading.Event(), mcp_client=object())
        self.assertEqual((await self.handler.handle(self.request(body))).status, 409)
        self.assertFalse(any(name == 'self.upgrade_firmware' for _, name, _ in self.sent))

    async def test_old_versions_wrong_board_and_invalid_urls_are_rejected(self):
        for change in [{'version': '2.2.9'}, {'version': '1.2.0'}, {'boardType': 'wrong'},
                       {'url': 'file:///firmware'}, {'url': 'http://host/wrong'}]:
            body = await self.start_body(); body.update(change)
            self.assertEqual((await self.handler.handle(self.request(body))).status, 409)
        self.assertFalse(any(name == 'self.upgrade_firmware' for _, name, _ in self.sent))

    async def test_chassis_upgrade_blocks_esp32_upgrade(self):
        body = await self.start_body(); self.chassis_active = True
        self.assertEqual((await self.handler.handle(self.request(body))).status, 409)

    async def test_timeout_is_not_replayed_or_reported_as_success(self):
        body = await self.start_body()
        original = self.handler.call.side_effect
        async def timeout(conn, name, args):
            if name == 'self.upgrade_firmware':
                raise TimeoutError()
            return await original(conn, name, args)
        self.handler.call.side_effect = timeout
        state = json.loads((await self.handler.handle(self.request(body))).body)['data']
        self.assertTrue(state['active']); self.assertEqual(state['phase'], 'unconfirmed')
        self.handler.deadline = 0
        self.assertFalse((await self.handler.state())['active'])

    async def test_push_progress_does_not_poll_the_busy_application_or_fake_completion(self):
        await self.handler.handle(self.request(await self.start_body()))
        event = {'jobId': 'operation-1', 'phase': 'downloading', 'progress': 42}
        self.assertTrue(self.handler.accept_progress(self.conn, event))
        calls = self.handler.call.await_count
        state = await self.handler.state()
        self.assertEqual(state['progress'], 42)
        self.assertEqual(self.handler.call.await_count, calls)
        self.assertTrue(self.handler.accept_progress(self.conn, dict(event, phase='rebooting', progress=100)))
        state = await self.handler.state()
        self.assertEqual(state['progress'], 99); self.assertTrue(state['active'])
        self.server.device_handlers['robot'] = NS(stop_event=threading.Event(), mcp_client=object())
        self.version = '2.2.10'
        state = await self.handler.state()
        self.assertEqual(state['progress'], 100); self.assertEqual(state['phase'], 'completed')

    async def test_progress_is_bound_to_job_and_connection_and_must_be_monotonic(self):
        await self.handler.handle(self.request(await self.start_body()))
        event = {'jobId': 'operation-1', 'phase': 'downloading', 'progress': 42}
        self.assertTrue(self.handler.accept_progress(self.conn, event))
        for change in [{'jobId': 'other'}, {'progress': True}, {'progress': -1},
                       {'progress': 101}, {'progress': 30}, {'phase': 'completed'}]:
            self.assertFalse(self.handler.accept_progress(self.conn, dict(event, **change)))
        self.assertEqual(self.handler.job['progress'], 42)
        self.server.device_handlers['robot'] = NS(stop_event=threading.Event(), mcp_client=object())
        self.assertFalse(self.handler.accept_progress(self.conn, event))

    async def test_reported_failure_is_terminal(self):
        await self.handler.handle(self.request(await self.start_body()))
        self.assertTrue(self.handler.accept_progress(self.conn, {'jobId': 'operation-1', 'phase': 'failed', 'progress': 0}))
        self.assertFalse(self.handler.job['active'])
        self.assertFalse(self.handler.accept_progress(self.conn, {'jobId': 'operation-1', 'phase': 'downloading', 'progress': 10}))

    async def test_websocket_progress_reaches_the_http_state_cache(self):
        from core.handle.textMessageProcessor import TextMessageProcessor
        await self.handler.handle(self.request(await self.start_body()))
        self.server.esp32_ota_handler = self.handler
        self.conn.server = self.server
        self.conn.logger = MagicMock()
        await TextMessageProcessor(None).process_message(self.conn, json.dumps({
            'type': 'ota_progress', 'jobId': 'operation-1', 'phase': 'downloading', 'progress': 65,
        }))
        state = await self.handler.state()
        self.assertEqual(state['progress'], 65)
        self.assertEqual(state['phase'], 'downloading')


if __name__ == '__main__':
    unittest.main()
