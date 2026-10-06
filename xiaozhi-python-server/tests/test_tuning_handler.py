"""Mocked operator tuning contracts; no physical device access."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from core.api.tuning_handler import TuningHandler, encode_parameters

PARAMS = dict(midAngle=2.1, balanceKp=9600, balanceKd=50, velocityKp=6200, velocityKi=15, turnKd=6)
CALL = 'core.providers.tools.device_mcp.mcp_handler.call_mcp_tool'


class TuningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = SimpleNamespace(stop_event=SimpleNamespace(is_set=lambda: False), mcp_client=object())
        self.server = SimpleNamespace(device_handlers={'device': self.conn})
        self.handler = TuningHandler(self.server)
        self.handler.authorized = lambda request: True

    def request(self, **overrides):
        body = dict(deviceId='device', operation='apply', revision=1, tick=100, params=PARAMS)
        body.update(overrides)
        return SimpleNamespace(method='POST', json=AsyncMock(return_value=body))

    def test_scaled_precision_and_bool_are_rejected(self):
        self.assertEqual(encode_parameters(PARAMS), '2100,960000,5000,620000,1500,600')
        for value in (True, float('nan'), float('inf'), 1.001, 201):
            with self.assertRaises(ValueError):
                encode_parameters({**PARAMS, 'balanceKd': value})

    async def test_unauthorized_request_never_calls_device(self):
        self.handler.authorized = lambda request: False
        with patch(CALL, new_callable=AsyncMock) as call:
            response = await self.handler.handle(self.request())
            self.assertEqual(response.status, 401)
            call.assert_not_awaited()

    async def test_bad_revision_and_sample_clock_are_rejected(self):
        for change in (dict(revision=True), dict(revision=0), dict(tick=-1), dict(tick=2147483648)):
            with patch(CALL, new_callable=AsyncMock) as call:
                response = await self.handler.handle(self.request(**change))
                self.assertEqual(response.status, 400)
                call.assert_not_awaited()

    async def test_conflict_timeout_and_mismatch_never_retry(self):
        for payload in (
            json.dumps({'ok': False, 'errorCode': 4}),
            json.dumps({'ok': True, 'state': {'valid': True, 'params': {**PARAMS, 'balanceKd': 51}}}),
        ):
            with patch(CALL, new_callable=AsyncMock, return_value=payload) as call:
                response = await self.handler.handle(self.request())
                self.assertEqual(response.status, 400)
                self.assertEqual(call.await_count, 1)
        with patch(CALL, new_callable=AsyncMock, side_effect=TimeoutError) as call:
            self.assertEqual((await self.handler.handle(self.request())).status, 503)
            self.assertEqual(call.await_count, 1)

    async def test_busy_mutation_does_not_queue(self):
        lock = self.handler.locks['device'] = asyncio.Lock()
        await lock.acquire()
        try:
            with patch(CALL, new_callable=AsyncMock) as call:
                self.assertEqual((await self.handler.handle(self.request())).status, 400)
                call.assert_not_awaited()
        finally:
            lock.release()

    async def test_connection_replacement_invalidates_confirmation(self):
        async def replace(*args, **kwargs):
            self.server.device_handlers['device'] = object()
            return json.dumps({'ok': True, 'state': {'valid': True, 'params': PARAMS}})
        with patch(CALL, new_callable=AsyncMock, side_effect=replace):
            self.assertEqual((await self.handler.handle(self.request())).status, 400)

    async def test_operator_only_exact_actual_readback(self):
        payload = json.dumps({'ok': True, 'state': {'valid': True, 'params': PARAMS}})
        with patch(CALL, new_callable=AsyncMock, return_value=payload) as call:
            self.assertEqual((await self.handler.handle(self.request())).status, 200)
            self.assertEqual(call.await_args.args[2], 'self.chassis.tuning')
            self.assertIs(call.await_args.kwargs['operator_only'], True)
            self.assertEqual(json.loads(call.await_args.args[3])['values'], encode_parameters(PARAMS))

    async def test_read_uses_separate_readonly_tool_during_motion(self):
        request = SimpleNamespace(method='GET', query={'deviceId': 'device', 'after': '12', 'epoch': '3'})
        with patch(CALL, new_callable=AsyncMock, return_value=json.dumps({'ok': True, 'state': {}})) as call:
            self.assertEqual((await self.handler.handle(request)).status, 200)
            self.assertEqual(call.await_args.args[2], 'self.chassis.tuning_state')
            self.assertEqual(json.loads(call.await_args.args[3])['after'], 12)

    async def test_save_and_load_require_saved_actual_values_to_match(self):
        payload = json.dumps({'ok': True, 'state': {'valid': True, 'params': PARAMS,
                                                   'saved': {**PARAMS, 'balanceKd': 51}}})
        for operation in ('save', 'load'):
            with patch(CALL, new_callable=AsyncMock, return_value=payload) as call:
                self.assertEqual((await self.handler.handle(self.request(operation=operation))).status, 400)
                self.assertEqual(call.await_count, 1)

    async def test_gyro_start_requires_explicit_stationary_confirmation(self):
        for confirmed in (None, False, 1):
            with patch(CALL, new_callable=AsyncMock) as call:
                response = await self.handler.handle(self.request(operation='gyro_start', confirmed=confirmed))
                self.assertEqual(response.status, 400)
                call.assert_not_awaited()

    async def test_gyro_start_confirms_collection_not_calibration_success(self):
        for status, valid, expected in ((1, True, 200), (2, True, 400), (3, True, 400), (1, False, 400)):
            payload = json.dumps({'ok': True, 'state': {'valid': True, 'params': PARAMS,
                'gyro': {'valid': valid, 'status': status, 'calibrated': False}}})
            with patch(CALL, new_callable=AsyncMock, return_value=payload) as call:
                response = await self.handler.handle(self.request(operation='gyro_start', confirmed=True))
                self.assertEqual(response.status, expected)
                self.assertEqual(call.await_count, 1)
                self.assertEqual(json.loads(call.await_args.args[3])['operation'], 'gyro_start')

    async def test_gyro_cancel_and_clear_require_actual_confirmation(self):
        for operation, status, calibrated, expected in (
            ('gyro_cancel', 3, True, 200), ('gyro_cancel', 1, True, 400),
            ('gyro_clear', 0, False, 200), ('gyro_clear', 0, True, 400), ('gyro_clear', 2, False, 400),
        ):
            payload = json.dumps({'ok': True, 'state': {'valid': True, 'params': PARAMS,
                'gyro': {'valid': True, 'status': status, 'calibrated': calibrated}}})
            with patch(CALL, new_callable=AsyncMock, return_value=payload):
                self.assertEqual((await self.handler.handle(self.request(operation=operation))).status, expected)


if __name__ == '__main__':
    unittest.main()
