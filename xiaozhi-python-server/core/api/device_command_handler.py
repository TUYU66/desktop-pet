"""设备控制 + 配置热重载 API"""

import json
import traceback
from aiohttp import web
from config.logger import setup_logging
from config.config_loader import load_remote_config
from core.utils.service_auth import device_service_authorized

TAG = __name__


class DeviceCommandHandler:
    """处理 Java 后端转发的设备控制命令和配置重载"""

    def __init__(self, config: dict, ws_server=None):
        self.config = config
        self.ws_server = ws_server
        self.logger = setup_logging()

    async def handle_command(self, request: web.Request) -> web.Response:
        """接收设备控制命令，通过 WebSocket 转发给对应设备"""
        if not device_service_authorized(request):
            return web.json_response({'code': 403, 'msg': '无权访问设备控制'}, status=403)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                return web.json_response({'code': 400, 'msg': '命令格式无效'}, status=400)
            device_id = body.get("deviceId")
            tool_name = body.get("tool")
            args = body.get("args", {})

            if not isinstance(device_id, str) or not device_id or not isinstance(tool_name, str) or not tool_name:
                return web.json_response({"code": 400, "msg": "deviceId 和 tool 不能为空"}, status=400)
            if not self.ws_server:
                return web.json_response({"code": 500, "msg": "WebSocket 服务未就绪"}, status=500)

            handler = self.ws_server.device_handlers.get(device_id)
            if not handler:
                return web.json_response({"code": 404, "msg": f"设备 {device_id} 不在线"}, status=404)

            import uuid
            mcp_id = str(uuid.uuid4())
            if not isinstance(args, dict) or tool_name == 'self.chassis.calibration':
                return web.json_response({'code': 400, 'msg': '请使用专用控制面板'}, status=400)
            from core.api.volume_handler import VolumeHandler
            try:
                result = await VolumeHandler()._call(handler, tool_name, args)
                return web.json_response({'code': 0, 'msg': '设备已返回结果，请以实际结果为准',
                    'data': {'id': mcp_id, 'tool': tool_name, 'confirmation': 'device_ack', 'result': result}})
            except Exception:
                return web.json_response({'code': 503, 'msg': '未取得设备结果，请核实；不会自动重发',
                    'data': {'id': mcp_id, 'confirmation': 'unknown'}}, status=503)

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"命令处理失败: {e}")
            return web.json_response({"code": 500, "msg": str(e)}, status=500)

    async def handle_reload(self, request: web.Request) -> web.Response:
        """热重载用户配置（prompt/wakeWords/voice），由 Java 后端在保存配置后调用"""
        if not device_service_authorized(request):
            return web.json_response({'code': 403, 'msg': '无权更新设备配置'}, status=403)
        try:
            body = await request.json() if request.can_read_body else {}
            if not isinstance(body, dict):
                return web.json_response({'code': 400, 'msg': '配置格式无效'}, status=400)
            # 从 Java 拉取最新配置合并到当前 runtime config
            await load_remote_config(self.config)

            if body.get("wakeOnly"):
                return web.json_response({'code': 0, 'msg': '配置已保存，等待设备确认'})

            # 通知所有在线设备的 ConnectionHandler 刷新配置
            if self.ws_server:
                import copy
                import asyncio

                fresh = copy.deepcopy(self.config)
                device_count = 0
                for device_id, handler in list(self.ws_server.device_handlers.items()):
                    device_count += 1
                    try:
                        handler.pending_runtime_config = fresh
                        handler.runtime_config_status = 'pending'
                        task = getattr(handler, '_config_apply_task', None)
                        if task is None or task.done():
                            handler._config_apply_task = asyncio.create_task(self._apply_when_idle(handler))
                        self.logger.bind(tag=TAG).info(f'设备 {device_id} 配置将在本轮结束后应用')
                    except Exception as e:
                        self.logger.bind(tag=TAG).error(f'推送配置到 {device_id} 失败: {e}')

                self.logger.bind(tag=TAG).info(f'配置热重载完成，已处理 {device_count} 个设备')
            else:
                self.logger.bind(tag=TAG).warning('WebSocket 服务未就绪，仅更新了全局配置')
            return web.json_response({'code': 0, 'msg': '配置已保存，将在当前交互结束后应用'})

        except Exception as e:
            self.logger.bind(tag=TAG).error(f'配置重载失败: {e}')
            return web.json_response({'code': 500, 'msg': str(e)}, status=500)

    async def _apply_when_idle(self, handler):
        import asyncio
        from core.utils.prompt_manager import PromptManager
        try:
            while not handler.stop_event.is_set():
                pending = getattr(handler, 'pending_runtime_config', None)
                if pending is None: return
                music = getattr(handler, 'local_music', None)
                if (handler.client_is_speaking or getattr(handler, 'tts_finishing', False)
                        or getattr(handler, 'client_have_voice', False)
                        or music and music.state in ('playing', 'loading')
                        or not handler.chat_lock.acquire(blocking=False)):
                    await asyncio.sleep(.25)
                    continue
                try:
                    # Only a role change requires reinitializing role-scoped memory.
                    role_changed = pending.get('activeRole', handler.config.get('activeRole')) != handler.config.get('activeRole')
                    for key in ('prompt', 'wakeup_words', 'voice', 'activeRole', 'roles'):
                        if key in pending: handler.config[key] = pending[key]
                    handler.pending_runtime_config = None
                    if role_changed:
                        handler.runtime_config_status = 'reconnecting'
                        await handler.websocket.send(json.dumps({'type': 'status', 'text': '角色已切换，请重新唤醒。'}))
                        await handler.close()
                        return
                    handler.prompt_manager = PromptManager(handler.config, handler.logger)
                    if handler.config.get('prompt') is not None:
                        handler.change_system_prompt(handler.prompt_manager.get_quick_prompt(handler.config['prompt']))
                    if handler.tts and handler.config.get('voice'):
                        handler.tts.voice = handler.config['voice']
                    handler.runtime_config_status = 'applied'
                finally:
                    handler.chat_lock.release()
        except Exception as error:
            handler.runtime_config_status = 'failed'
            self.logger.bind(tag=TAG).warning('配置应用失败: {}', type(error).__name__)

    async def handle_list_devices(self, request: web.Request) -> web.Response:
        """返回在线设备列表"""
        if not device_service_authorized(request):
            return web.json_response({'code': 403, 'msg': '无权读取设备列表'}, status=403)
        devices = []
        if self.ws_server:
            for device_id, handler in self.ws_server.device_handlers.items():
                devices.append({"deviceId": device_id, "sessionId": handler.session_id,
                    "configState": getattr(handler, 'runtime_config_status', 'applied')})
        return web.json_response({"code": 0, "data": devices})
