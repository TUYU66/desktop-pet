"""Internal authenticated bridge to operator-only STM32 OTA tools."""
import json
import re
from urllib.parse import urlsplit
from aiohttp import web
from core.api.volume_handler import VolumeHandler
from core.utils.service_auth import device_service_authorized


class Stm32OtaHandler(VolumeHandler):
    async def handle(self, request):
        if not device_service_authorized(request):
            return web.json_response({'code': 401, 'msg': '设备服务认证失败'}, status=401)
        try:
            devices = [(key, conn) for key, conn in self.ws_server.device_handlers.items() if not conn.stop_event.is_set()]
            if len(devices) != 1:
                return web.json_response({'code': 409, 'msg': '需要且只能有一台在线设备'}, status=409)
            device, conn = devices[0]
            args = {}
            name = 'self.chassis.ota_state'
            if request.method == 'POST':
                esp32 = getattr(self.ws_server, 'esp32_ota_handler', None)
                if esp32 and esp32.job and esp32.job.get('active'):
                    raise ValueError('ESP32 正在升级，请等待确认完成')
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError('升级请求格式错误')
                job, url, metadata = body.get('jobId'), body.get('url'), body.get('metadata')
                if not isinstance(job, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', job):
                    raise ValueError('升级任务编号无效')
                if not isinstance(url, str) or len(url) > 512:
                    raise ValueError('固件下载地址无效')
                parsed = urlsplit(url)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not re.fullmatch(r'/xiaozhi/ota/stm32/download/[0-9a-f]{32}', parsed.path):
                    raise ValueError('固件下载地址无效')
                if not isinstance(metadata, dict) or len(json.dumps(metadata)) > 1024:
                    raise ValueError('固件信息无效')
                name = 'self.chassis.ota_start'
                args = {'job_id': job, 'url': url, 'metadata': json.dumps(metadata)}
            from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
            result = json.loads(await call_mcp_tool(conn, conn.mcp_client, name, json.dumps(args), timeout=6, operator_only=True))
            if self.ws_server.device_handlers.get(device) is not conn or conn.stop_event.is_set():
                raise RuntimeError('设备连接已变化，升级结果未确认')
            if not isinstance(result, dict):
                raise ValueError('设备升级状态格式错误')
            # A terminal job's error is valid state, not a failed status read.
            # Returning 409 here leaves the browser showing its last active 18%.
            if result.get('error') and not isinstance(result.get('phase'), str):
                return web.json_response({'code': 409, 'msg': result['error']}, status=409)
            return web.json_response({'code': 0, 'data': result})
        except ValueError as error:
            return web.json_response({'code': 400, 'msg': str(error)}, status=400)
        except Exception:
            return web.json_response({'code': 503, 'msg': '设备升级服务未就绪，请确认 ESP32 固件并刷新状态；不要重复点击升级'}, status=503)
