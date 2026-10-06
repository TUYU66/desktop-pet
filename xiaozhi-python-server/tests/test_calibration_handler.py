"""Operator calibration regressions; mocks only, no physical device access."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.api.calibration_handler import CalibrationHandler


class CalibrationHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = SimpleNamespace(stop_event=SimpleNamespace(is_set=lambda: False), mcp_client=object())
        self.handler = CalibrationHandler(SimpleNamespace(device_handlers={'device': self.conn}))
        self.handler.authorized = lambda request: True

    def request(self, **args):
        return SimpleNamespace(json=AsyncMock(return_value={'deviceId': 'device', **args}))

    async def test_arm_requires_explicit_bench_confirmation(self):
        with patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool', new_callable=AsyncMock) as call:
            response = await self.handler.handle(self.request(operation='arm'))
            self.assertEqual(response.status, 400)
            call.assert_not_awaited()

    async def test_boolean_output_is_not_an_integer_command(self):
        with patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool', new_callable=AsyncMock) as call:
            response = await self.handler.handle(self.request(operation='set', pwm=True, session=4))
            self.assertEqual(response.status, 400)
            call.assert_not_awaited()

    async def test_no_nonzero_command_without_session(self):
        with patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool', new_callable=AsyncMock) as call:
            response = await self.handler.handle(self.request(operation='set', pwm=500))
            self.assertEqual(response.status, 400)
            call.assert_not_awaited()

    async def test_invalid_sample_clock_never_reaches_the_device(self):
        for tick in (True, -1, 2147483648):
            with self.subTest(tick=tick):
                with patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool', new_callable=AsyncMock) as call:
                    response = await self.handler.handle(self.request(operation='set', pwm=500, session=4, sample_tick=tick))
                    self.assertEqual(response.status, 400)
                    call.assert_not_awaited()

    async def test_timeout_is_not_retried(self):
        with patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool', new_callable=AsyncMock, side_effect=TimeoutError) as call:
            response = await self.handler.handle(self.request(operation='set', pwm=500, session=4))
            self.assertEqual(response.status, 503)
            self.assertEqual(call.await_count, 1)

    async def test_busy_request_is_rejected_instead_of_queued(self):
        self.handler.locks['device'] = asyncio.Lock()
        await self.handler.locks['device'].acquire()
        try:
            with patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool', new_callable=AsyncMock) as call:
                response = await self.handler.handle(self.request(operation='set', pwm=500, session=4))
                self.assertEqual(response.status, 400)
                call.assert_not_awaited()
        finally:
            self.handler.locks['device'].release()

    async def test_exit_bypasses_request_lock_and_uses_hidden_operator_tool(self):
        self.handler.locks['device'] = asyncio.Lock()
        await self.handler.locks['device'].acquire()
        state = {'valid': True, 'active': 0, 'pwm': 0}
        try:
            with patch('core.providers.tools.device_mcp.mcp_handler.call_mcp_tool', new_callable=AsyncMock,
                       return_value=json.dumps({'ok': True, 'state': state})) as call:
                response = await self.handler.handle(self.request(operation='exit'))
                self.assertEqual(response.status, 200)
                self.assertEqual(call.await_args.args[2], 'self.chassis.calibration')
                self.assertIs(call.await_args.kwargs['operator_only'], True)
        finally:
            self.handler.locks['device'].release()


if __name__ == '__main__':
    unittest.main()
