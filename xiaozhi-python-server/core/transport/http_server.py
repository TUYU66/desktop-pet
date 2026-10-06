import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.api.device_command_handler import DeviceCommandHandler
from core.api.chat_handler import ChatHandler
from core.api.volume_handler import VolumeHandler
from core.api.device_panel_handler import DevicePanelHandler
from core.api.calibration_handler import CalibrationHandler
from core.api.tuning_handler import TuningHandler
from core.api.music_handler import MusicHandler
from core.api.netease_handler import NeteaseHandler
from core.device.wake_word import WakeWordSync
from core.api.reminder_handler import ReminderHandler

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict, ws_server=None):
        self.config = config
        self.logger = setup_logging()
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)
        self.device_command_handler = DeviceCommandHandler(config, ws_server)
        self.chat_handler = ChatHandler(config, ws_server)
        self.volume_handler = VolumeHandler(ws_server)
        self.device_panel_handler = DevicePanelHandler(ws_server)
        self.calibration_handler = CalibrationHandler(ws_server)
        self.tuning_handler = TuningHandler(ws_server)
        self.music_handler = MusicHandler(config, ws_server)
        self.netease_handler = NeteaseHandler(config, ws_server, self.music_handler)
        self.wake_word_sync = WakeWordSync(config, ws_server)
        self.reminder_handler = ReminderHandler(ws_server)

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """获取websocket地址

        Args:
            local_ip: 本地IP地址
            port: 端口号

        Returns:
            str: websocket地址
        """
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")

        if websocket_config and "你" not in websocket_config:
            return websocket_config
        else:
            return f"ws://{local_ip}:{port}/xiaozhi/v1/"

    async def start(self):
        try:
            server_config = self.config["server"]
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))

            if port:
                app = web.Application()
                app.add_routes(
                    [
                        # OTA 接口
                        web.get("/xiaozhi/ota/", self.ota_handler.handle_get),
                        web.post("/xiaozhi/ota/", self.ota_handler.handle_post),
                        web.options("/xiaozhi/ota/", self.ota_handler.handle_options),
                        web.get("/xiaozhi/ota/download/{filename}", self.ota_handler.handle_download),
                        web.options("/xiaozhi/ota/download/{filename}", self.ota_handler.handle_options),
                        # 视觉分析
                        web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                        web.post("/mcp/vision/explain", self.vision_handler.handle_post),
                        web.options("/mcp/vision/explain", self.vision_handler.handle_options),
                        # 设备控制 API
                        web.get("/xiaozhi/device/wake-word/status", self.wake_word_sync.status),
                        web.post("/xiaozhi/device/calibration", self.calibration_handler.handle),
                        web.get("/xiaozhi/device/tuning", self.tuning_handler.handle),
                        web.post("/xiaozhi/device/tuning", self.tuning_handler.handle),
                        web.get("/xiaozhi/device/panel", self.device_panel_handler.handle),
                        web.post("/xiaozhi/device/panel", self.device_panel_handler.handle),
                        web.get("/xiaozhi/device/volume", self.volume_handler.handle),
                        web.put("/xiaozhi/device/volume", self.volume_handler.handle),
                        web.post("/xiaozhi/device/command", self.device_command_handler.handle_command),
                        web.post("/xiaozhi/config/reload", self.device_command_handler.handle_reload),
                        web.get("/xiaozhi/device/list", self.device_command_handler.handle_list_devices),
                        web.get("/xiaozhi/music", self.music_handler.handle),
                        web.get("/xiaozhi/music/status", self.music_handler.handle_status),
                        web.get("/xiaozhi/music/details/{track_id}", self.music_handler.handle),
                        web.put("/xiaozhi/music", self.music_handler.handle),
                        web.post("/xiaozhi/music/control", self.music_handler.handle),
                        web.get("/xiaozhi/music/netease/{operation}", self.netease_handler.handle),
                        web.post("/xiaozhi/music/netease/{operation}", self.netease_handler.handle),
                        web.delete("/xiaozhi/music/netease/{operation}", self.netease_handler.handle),
                        web.delete("/xiaozhi/music/{track_id}", self.music_handler.handle),
                        # 文字输入 + 记忆管理
                        web.post("/xiaozhi/chat/send", self.chat_handler.handle_send),
                        web.get("/xiaozhi/chat/requests/{requestId}", self.chat_handler.handle_request_status),
                        web.post("/xiaozhi/reminders/announce", self.reminder_handler.handle_announce),
                        web.delete("/xiaozhi/memory/{sessionId}", self.chat_handler.handle_clear_memory),
                    ]
                )

                # 运行服务
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()

                # Keep the reconciliation loop alive with the HTTP service.
                await self.wake_word_sync.run()
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP服务器启动失败: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            raise
