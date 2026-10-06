"""Authenticated operator-only bench calibration. No motion retries or ramps."""
import asyncio
import json

from aiohttp import web
from core.api.device_panel_handler import DevicePanelHandler


ERRORS = {
    1: '姿态或校准状态尚未就绪，请核对两块固件并刷新',
    2: '电池电压不足，不能校准',
    3: '倾角超出允许范围，请固定车身',
    4: '请先坐下并停止电机，再进入校准',
    5: '设备正在执行其他操作，请停止后重试',
    6: '校准会话或采样已过期，请刷新并重新进入校准',
    7: '轮子或输出数值超出允许范围',
    8: '试转已限时停止，请松开试转按钮再重新进入校准',
    9: '请先确认车身已固定、两个轮子均悬空',
    10: '没有获得完整执行确认；请停止操作并刷新状态，设备会限时停轮',
}


class CalibrationHandler(DevicePanelHandler):
    async def handle(self, request):
        if not self.authorized(request):
            return web.json_response({'code': 401, 'msg': '设备服务认证失败'}, status=401)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('请求格式错误')
            device, operation = body.get('deviceId'), body.get('operation')
            if not isinstance(device, str) or operation not in ('arm', 'set', 'exit'):
                raise ValueError('设备或操作无效')
            wheel, pwm, session = body.get('wheel', 1), body.get('pwm', 0), body.get('session', 0)
            if type(wheel) is not int or wheel not in (1, 2):
                raise ValueError('请选择左轮或右轮')
            if type(pwm) is not int or not -2600 <= pwm <= 2600:
                raise ValueError('输出必须为 -2600 至 2600 的整数')
            if type(session) is not int or not 0 <= session <= 65535 or operation == 'set' and session == 0:
                raise ValueError('校准会话无效，请重新进入校准')
            sample_tick = body.get('sample_tick', 0)
            if type(sample_tick) is not int or not 0 <= sample_tick <= 2147483647:
                raise ValueError('校准采样时间无效，请刷新状态')
            if operation == 'arm' and body.get('confirmed') is not True:
                raise ValueError(ERRORS[9])
            conn = self.ws_server.device_handlers.get(device) if self.ws_server else None
            if not conn or not self.connected(device, conn):
                raise ValueError('设备已离线，电机会由本地限时保护停止')
            lock = self.locks.setdefault(device, asyncio.Lock())
            # Never queue a delayed positive PWM behind another request. EXIT
            # can bypass this HTTP lock; the MCU still owns the stop deadline.
            if operation != 'exit' and lock.locked():
                raise ValueError('上一个校准请求未结束，停止后再试')

            async def call():
                from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
                client = getattr(conn, 'mcp_client', None)
                if client is None or not self.connected(device, conn):
                    raise ValueError('设备连接已变化，请刷新')
                args = {'operation': operation, 'wheel': wheel, 'pwm': pwm,
                        'confirmed': body.get('confirmed') is True, 'session': session, 'sample_tick': sample_tick}
                result = json.loads(await call_mcp_tool(
                    conn, client, 'self.chassis.calibration', json.dumps(args),
                    timeout=3, operator_only=True))
                if not self.connected(device, conn):
                    raise ValueError('设备连接已变化，执行结果待核实')
                if result.get('ok') is not True:
                    raise ValueError(ERRORS.get(result.get('errorCode'), ERRORS[10]))
                state = result.get('state')
                if not isinstance(state, dict) or state.get('valid') is not True:
                    raise ValueError(ERRORS[10])
                return web.json_response({'code': 0, 'data': {'state': state}})

            if operation == 'exit':
                return await call()
            async with lock:
                return await call()
        except ValueError as exc:
            return web.json_response({'code': 400, 'msg': str(exc)}, status=400)
        except Exception:
            return web.json_response({'code': 503, 'msg': ERRORS[10]}, status=503)
