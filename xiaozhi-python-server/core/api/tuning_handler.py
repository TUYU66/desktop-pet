"""Manual tuning and bounded telemetry; never exposed to conversational tools."""
import asyncio
import json
import math

from aiohttp import web
from core.api.device_panel_handler import DevicePanelHandler

PARAMETERS = {
    'midAngle': (-10, 10, 1000),
    'balanceKp': (1000, 20000, 100),
    'balanceKd': (0, 200, 100),
    'velocityKp': (0, 12000, 100),
    'velocityKi': (0, 100, 100),
    'turnKd': (0, 60, 100),
}
ERRORS = {
    1: '调参状态尚未就绪，请核对两块固件并刷新',
    2: '请先坐下、退出蓝牙控制和电机校准，待动作结束后再应用参数',
    3: '参数超出允许范围或精度，请检查输入',
    4: '设备参数已变化，请重新读取后再操作',
    5: '设备采样已过期，请重新读取后再操作',
    6: '没有可恢复或已保存的参数',
    7: '未获得完整确认，结果待核实；请刷新参数，不要重复应用',
    8: '参数保存未确认，请刷新查看已保存值',
}


def encode_parameters(params):
    if not isinstance(params, dict) or set(params) != set(PARAMETERS):
        raise ValueError(ERRORS[3])
    values = []
    for key, (lower, upper, scale) in PARAMETERS.items():
        value = params[key]
        if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
            raise ValueError(ERRORS[3])
        scaled = value * scale
        if abs(scaled - round(scaled)) > 1e-6:
            raise ValueError(ERRORS[3])
        values.append(str(round(scaled)))
    return ','.join(values)


class TuningHandler(DevicePanelHandler):
    async def handle(self, request):
        if not self.authorized(request):
            return web.json_response({'code': 401, 'msg': '设备服务认证失败'}, status=401)
        try:
            if request.method == 'GET':
                body = dict(request.query)
                operation = 'get'
                try:
                    after, epoch = int(body.get('after', '0')), int(body.get('epoch', '0'))
                except (ValueError, TypeError):
                    raise ValueError('曲线读取位置无效') from None
                if not 0 <= after <= 2147483647 or not 0 <= epoch <= 2147483647:
                    raise ValueError('曲线读取位置无效')
                args = {'operation': operation, 'after': after, 'epoch': epoch}
            else:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError('请求格式错误')
                operation = body.get('operation')
                if operation not in ('apply', 'undo', 'save', 'load', 'gyro_start', 'gyro_cancel', 'gyro_clear'):
                    raise ValueError('不支持的调参操作')
                if operation == 'gyro_start' and body.get('confirmed') is not True:
                    raise ValueError('请确认小车已坐下、电机停止且机身静止')
                revision, tick = body.get('revision'), body.get('tick')
                if type(revision) is not int or not 1 <= revision <= 2147483646:
                    raise ValueError(ERRORS[4])
                if type(tick) is not int or not 0 <= tick <= 2147483647:
                    raise ValueError(ERRORS[5])
                args = {'operation': operation, 'revision': revision, 'sample_tick': tick}
                if operation == 'apply':
                    args['values'] = encode_parameters(body.get('params'))
            device = body.get('deviceId')
            if not isinstance(device, str) or not device:
                raise ValueError('设备无效')
            conn = self.ws_server.device_handlers.get(device) if self.ws_server else None
            if not conn or not self.connected(device, conn):
                raise ValueError('设备已离线，请刷新状态')
            lock = self.locks.setdefault(device, asyncio.Lock())
            if lock.locked():
                raise ValueError('上一个调参请求尚未结束，请稍后读取状态')
            async with lock:
                from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
                client = getattr(conn, 'mcp_client', None)
                if client is None or not self.connected(device, conn):
                    raise ValueError('设备连接已变化，请刷新')
                tool = 'self.chassis.tuning_state' if operation == 'get' else 'self.chassis.tuning'
                raw = await call_mcp_tool(conn, client, tool, json.dumps(args),
                                          timeout=3, operator_only=True)
                result = json.loads(raw)
                if not self.connected(device, conn):
                    raise ValueError('设备连接已变化，执行结果待核实')
                if not isinstance(result, dict) or result.get('ok') is not True:
                    raise ValueError(ERRORS.get(result.get('errorCode') if isinstance(result, dict) else 7, ERRORS[7]))
                state = result.get('state')
                if not isinstance(state, dict):
                    raise ValueError(ERRORS[7])
                if operation != 'get':
                    if state.get('valid') is not True:
                        raise ValueError(ERRORS[7])
                    if operation == 'apply' and encode_parameters(state.get('params')) != args['values']:
                        raise ValueError(ERRORS[7])
                    if operation.startswith('gyro_'):
                        gyro = state.get('gyro')
                        if not isinstance(gyro, dict) or gyro.get('valid') is not True:
                            raise ValueError(ERRORS[7])
                        if operation == 'gyro_start' and gyro.get('status') != 1:
                            raise ValueError('校准未进入采集状态，请读取状态核实')
                        if operation == 'gyro_cancel' and gyro.get('status') == 1:
                            raise ValueError('取消校准未确认，请读取状态核实')
                        if operation == 'gyro_clear' and (gyro.get('status') != 0 or gyro.get('calibrated') is not False):
                            raise ValueError('清除校准未确认，请读取状态核实')
                    if operation in ('save', 'load') and encode_parameters(state.get('saved')) != encode_parameters(state.get('params')):
                        raise ValueError(ERRORS[8] if operation == 'save' else ERRORS[7])
                return web.json_response({'code': 0, 'data': state})
        except ValueError as exc:
            return web.json_response({'code': 400, 'msg': str(exc)}, status=400)
        except Exception:
            return web.json_response({'code': 503, 'msg': ERRORS[7]}, status=503)
