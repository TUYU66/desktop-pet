"""Read and set volume with an ESP32 acknowledgement, never just send-and-forget."""
import json
from aiohttp import web


class VolumeHandler:
    def __init__(self, ws_server=None):
        self.ws_server = ws_server

    async def _call(self, handler, name, arguments):
        from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
        client = getattr(handler, "mcp_client", None)
        if client is None:
            raise RuntimeError("设备控制尚未就绪")
        mapped = next((key for key, value in client.name_mapping.items() if value == name), name)
        return await call_mcp_tool(handler, client, mapped, json.dumps(arguments), timeout=5)

    async def handle(self, request):
        try:
            volume = None
            if request.method == "PUT":
                body = await request.json()
                volume = body.get("volume") if isinstance(body, dict) else None
                if type(volume) is not int or not 0 <= volume <= 100:
                    return web.json_response({"code": 400, "msg": "音量必须为 0～100 的整数"}, status=400)
            devices = self.ws_server.device_handlers if self.ws_server else {}
            if len(devices) != 1:
                return web.json_response({"code": 409, "msg": "需要且只能有一台在线设备"}, status=409)
            handler = next(iter(devices.values()))
            # Always read before changing; read again to verify the actual device value.
            state = json.loads(await self._call(handler, "self.get_device_status", {}))
            current = state.get("audio_speaker", {}).get("volume")
            if type(current) is not int or not 0 <= current <= 100:
                raise RuntimeError("设备未返回有效音量")
            if volume is not None:
                await self._call(handler, "self.audio_speaker.set_volume", {"volume": volume})
                state = json.loads(await self._call(handler, "self.get_device_status", {}))
                current = state.get("audio_speaker", {}).get("volume")
                if type(current) is not int or current != volume:
                    raise RuntimeError("音量未确认，请重新读取设备状态")
            return web.json_response({"code": 0, "data": {"volume": current}})
        except (ValueError, TypeError):
            return web.json_response({"code": 400, "msg": "请求或设备响应格式错误"}, status=400)
        except Exception:
            return web.json_response({"code": 503, "msg": "设备未就绪或未确认音量，请重新读取"}, status=503)
