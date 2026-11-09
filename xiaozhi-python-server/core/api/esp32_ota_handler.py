"""Operator-only ESP32 OTA. An accepted MCP command is not installation success."""
import asyncio
import json
import re
import time
import uuid
from urllib.parse import urlsplit
from aiohttp import web
from core.api.volume_handler import VolumeHandler
from core.utils.service_auth import device_service_authorized


def newer(candidate, current):
    pattern = r"[0-9]+\.[0-9]+\.[0-9]+"
    return (isinstance(candidate, str) and isinstance(current, str)
            and re.fullmatch(pattern, candidate) and re.fullmatch(pattern, current)
            and tuple(map(int, candidate.split('.'))) > tuple(map(int, current.split('.'))))


class Esp32OtaHandler(VolumeHandler):
    def __init__(self, ws_server=None):
        super().__init__(ws_server)
        self.lock = asyncio.Lock()
        self.connection = None
        self.connection_id = ''
        self.job = None
        self.deadline = 0
        self.last_progress_at = 0

    async def call(self, conn, name, args):
        from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
        return json.loads(await call_mcp_tool(conn, conn.mcp_client, name,
                                             json.dumps(args), timeout=4, operator_only=True))

    def online(self):
        devices = [conn for conn in self.ws_server.device_handlers.values() if not conn.stop_event.is_set()]
        if len(devices) != 1:
            return None
        return devices[0]

    def accept_progress(self, conn, event):
        # 仅接收已确认连接、已保留任务的进度；过期连接或其他任务不得覆盖。
        if (conn is not self.connection or conn is not self.online() or not self.job
                or not self.job['active'] or event.get('jobId') != self.job['jobId']):
            return False
        phase, progress = event.get('phase'), event.get('progress')
        if phase not in ('queued', 'downloading', 'rebooting', 'failed') or type(progress) is not int or not 0 <= progress <= 100:
            return False
        previous = self.job.get('progress')
        if phase != 'failed' and previous is not None and progress < previous:
            return False
        self.job.update(phase=phase, progress=min(99, progress), progressAvailable=True)
        self.last_progress_at = time.monotonic()
        conn.last_transport_time = time.time() * 1000
        if phase == 'failed':
            self.job['active'] = False
        return True

    async def state(self):
        conn = self.online()
        base = {'ready': False, 'active': False, 'phase': 'idle'}
        if self.job:
            if self.job['active'] and time.monotonic() > self.deadline:
                self.job.update(active=False, phase='unconfirmed')
            base.update(self.job)
        if conn is None:
            return base
        if (self.job and self.job['active'] and conn is self.connection
                and time.monotonic() - self.last_progress_at < 15):
            return base
        try:
            info = await self.call(conn, 'self.get_system_info', {})
            if conn is not self.online():
                return base
            version = info['application']['version']
            board = info['board']['type']
            identity = info['mac_address']
            chassis = await self.call(conn, 'self.chassis.ota_state', {})
            if conn is not self.online():
                return base
            if conn is not self.connection:
                self.connection, self.connection_id = conn, uuid.uuid4().hex
            base.update(ready=True, currentVersion=version, boardType=board,
                        connectionId=self.connection_id, downloadOrigin=chassis.get('downloadOrigin', ''),
                        chassisActive=bool(chassis.get('active')))
            if self.job and self.job['phase'] != 'failed' and identity == self.job['identity'] and version == self.job['version']:
                self.job.update(active=False, phase='completed', progress=100)
                base.update(self.job)
        except Exception:
            # During flashing the application task may not answer; keep the pending job.
            pass
        return base

    async def handle(self, request):
        if not device_service_authorized(request):
            return web.json_response({'code': 401, 'msg': '设备服务认证失败'}, status=401)
        async with self.lock:
            try:
                if request.method == 'GET':
                    return web.json_response({'code': 0, 'data': await self.state()})
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError('升级请求格式错误')
                job_id = body.get('jobId')
                if self.job and job_id == self.job['jobId']:
                    return web.json_response({'code': 0, 'data': await self.state()})
                if self.job and self.job['active']:
                    raise ValueError('已有 ESP32 升级待确认，请刷新进度')
                conn = self.online()
                if conn is None or conn is not self.connection or body.get('connectionId') != self.connection_id:
                    raise ValueError('设备已重连，请刷新状态后重新确认升级')
                state = await self.state()
                if not state.get('ready') or conn is not self.online():
                    raise ValueError('设备未就绪，请刷新状态')
                version, url = body.get('version'), body.get('url')
                if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', job_id):
                    raise ValueError('升级任务编号无效')
                if not newer(version, state.get('currentVersion')) or body.get('boardType') != state.get('boardType'):
                    raise ValueError('固件须适配当前设备且高于已安装版本')
                if not isinstance(url, str) or len(url) > 512:
                    raise ValueError('固件地址无效')
                parsed = urlsplit(url)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not re.fullmatch(r'/xiaozhi/ota/download/[0-9a-f]{32}', parsed.path):
                    raise ValueError('固件地址无效')
                chassis = await self.call(conn, 'self.chassis.ota_state', {})
                if chassis.get('active'):
                    raise ValueError('底盘正在升级，请等待完成')
                info = await self.call(conn, 'self.get_system_info', {})
                if conn is not self.online():
                    raise ValueError('设备连接已变化，请重新确认')
                self.job = {'jobId': job_id, 'version': version, 'identity': info['mac_address'],
                            'active': True, 'phase': 'requested', 'progress': None, 'progressAvailable': False}
                self.deadline = time.monotonic() + 300
                # Reserve before sending. A timeout must not lead to automatic replay.
                try:
                    accepted = await self.call(conn, 'self.upgrade_firmware', {'url': url, 'version': version, 'job_id': job_id})
                    if accepted is not True:
                        self.job.update(active=False, phase='failed')
                except Exception:
                    if self.job['phase'] == 'requested':
                        self.job['phase'] = 'unconfirmed'
                return web.json_response({'code': 0, 'data': dict(self.job)})
            except (ValueError, KeyError, TypeError) as error:
                return web.json_response({'code': 409, 'msg': str(error)}, status=409)
            except Exception:
                return web.json_response({'code': 503, 'msg': '设备升级服务不可用，请刷新状态'}, status=503)
