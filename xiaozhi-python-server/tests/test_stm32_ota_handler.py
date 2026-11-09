import json
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from core.api.stm32_ota_handler import Stm32OtaHandler


class Stm32OtaHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict('os.environ', {'XIAOZHI_DEVICE_SERVICE_KEY': 'private-test'})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.conn = NS(stop_event=threading.Event(), mcp_client=object())
        self.handler = Stm32OtaHandler(NS(device_handlers={'single': self.conn}))
        self.call = patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool', new_callable=AsyncMock)
        self.mock = self.call.start(); self.addCleanup(self.call.stop)
        self.mock.return_value = json.dumps({'supported': True, 'active': True, 'phase': 'queued'})

    def request(self, body=None, authorized=True):
        return NS(method='POST' if body is not None else 'GET',
                  headers={'Service-Key': 'private-test'} if authorized else {}, json=AsyncMock(return_value=body))

    async def test_authentication_precedes_body_and_device_access(self):
        request = self.request({'jobId': 'x'}, False)
        self.assertEqual((await self.handler.handle(request)).status, 401)
        request.json.assert_not_awaited(); self.mock.assert_not_awaited()

    async def test_only_authenticated_operator_tools_are_dispatched(self):
        response = await self.handler.handle(self.request({'jobId': 'operation-1',
            'url': 'http://192.168.1.2:8000/xiaozhi/ota/stm32/download/' + 'a'*32,
            'metadata': {'firmware_version': 2}}))
        self.assertEqual(response.status, 200)
        self.assertEqual(self.mock.call_args.args[2], 'self.chassis.ota_start')
        self.assertIs(self.mock.call_args.kwargs['operator_only'], True)
        args = json.loads(self.mock.call_args.args[3])
        self.assertEqual(args['job_id'], 'operation-1')
        await self.handler.handle(self.request())
        self.assertEqual(self.mock.call_args.args[2], 'self.chassis.ota_state')

    async def test_invalid_sources_and_multiple_devices_do_not_start_updates(self):
        for url in ('file:///tmp/firmware', 'http://user:pass@host/xiaozhi/ota/stm32/download/'+'a'*32,
                    'http://host/other', 'http://host/xiaozhi/ota/stm32/download/'+'a'*32+'?token=x'):
            response = await self.handler.handle(self.request({'jobId': 'job', 'url': url, 'metadata': {}}))
            self.assertEqual(response.status, 400)
        self.mock.assert_not_awaited()
        self.handler.ws_server.device_handlers['other'] = self.conn
        self.assertEqual((await self.handler.handle(self.request())).status, 409)

    async def test_unknown_start_outcome_is_reported_without_replay(self):
        self.mock.side_effect = TimeoutError()
        response = await self.handler.handle(self.request())
        self.assertEqual(response.status, 503)
        self.assertEqual(self.mock.await_count, 1)

    async def test_failed_job_is_returned_as_terminal_state_not_a_read_failure(self):
        state={'supported':True,'active':False,'phase':'failed','progress':0,
               'error':'未确认停稳或电压低于 10.8V，升级未开始'}
        self.mock.return_value=json.dumps(state)
        response=await self.handler.handle(self.request())
        self.assertEqual(response.status,200)
        self.assertEqual(json.loads(response.body)['data'],state)

    async def test_start_rejection_without_job_state_remains_an_error(self):
        self.mock.return_value=json.dumps({'error':'已有底盘升级正在进行，请刷新进度'})
        self.assertEqual((await self.handler.handle(self.request())).status,409)

    async def test_connection_replacement_invalidates_upgrade_confirmation(self):
        async def replace(*args, **kwargs):
            self.handler.ws_server.device_handlers['single'] = NS(stop_event=threading.Event())
            return '{"active":false,"phase":"completed"}'
        self.mock.side_effect = replace
        self.assertEqual((await self.handler.handle(self.request())).status, 503)

    async def test_download_grant_is_sent_to_device_but_redacted_from_debug_log(self):
        from core.providers.tools.device_mcp.mcp_handler import send_mcp_message
        conn=NS(features={'mcp':True},websocket=NS(send=AsyncMock()))
        payload={'method':'tools/call','params':{'name':'self.chassis.ota_start','arguments':{'url':'secret-download-grant'}}}
        with patch('core.providers.tools.device_mcp.mcp_handler.logger') as logger:
            await send_mcp_message(conn,payload)
            logged=logger.bind.return_value.debug.call_args.args[0]
            self.assertNotIn('secret-download-grant',logged)
        self.assertIn('secret-download-grant',conn.websocket.send.call_args.args[0])
