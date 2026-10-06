import asyncio
import logging

import websockets
from config.logger import setup_logging


class SuppressInvalidHandshakeFilter(logging.Filter):
    """过滤掉无效握手错误日志（如HTTPS访问WS端口）"""

    def filter(self, record):
        msg = record.getMessage()
        suppress_keywords = [
            "opening handshake failed",
            "did not receive a valid HTTP request",
            "connection closed while reading HTTP request",
            "line without CRLF",
        ]
        return not any(keyword in msg for keyword in suppress_keywords)


def _setup_websockets_logger():
    filter_instance = SuppressInvalidHandshakeFilter()
    for logger_name in ["websockets", "websockets.server", "websockets.client"]:
        logger = logging.getLogger(logger_name)
        logger.addFilter(filter_instance)


_setup_websockets_logger()


from core.transport.connection import ConnectionHandler
from core.transport.auth import AuthManager, AuthenticationError
from core.utils.modules_initialize import initialize_modules
from core.utils import memory as memory_utils
from core.device.health import ServiceHealth

TAG = __name__


class WebSocketServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()
        self.device_handlers = {}  # device_id → ConnectionHandler
        self.service_health = ServiceHealth()
        modules = initialize_modules(
            self.logger,
            self.config,
            "VAD" in self.config["selected_module"],
            "ASR" in self.config["selected_module"],
            "LLM" in self.config["selected_module"],
            False,
            False,
            "Intent" in self.config["selected_module"],
        )
        self._vad = modules["vad"] if "vad" in modules else None
        self._asr = modules["asr"] if "asr" in modules else None
        self._llm = modules["llm"] if "llm" in modules else None
        self._intent = modules["intent"] if "intent" in modules else None

        auth_config = self.config["server"].get("auth", {})
        self.auth_enable = auth_config.get("enabled", False)
        self.allowed_devices = set(auth_config.get("allowed_devices", []))
        secret_key = self.config["server"]["auth_key"]
        expire_seconds = auth_config.get("expire_seconds", None)
        self.auth = AuthManager(secret_key=secret_key, expire_seconds=expire_seconds)

    async def start(self):
        server_config = self.config["server"]
        host = server_config.get("ip", "0.0.0.0")
        port = int(server_config.get("port", 8000))

        health_task = asyncio.create_task(self.service_health.run(), name="java-health")
        try:
            async with websockets.serve(
                self._handle_connection, host, port, process_request=self._http_response
            ):
                await asyncio.Future()
        finally:
            health_task.cancel()
            await asyncio.gather(health_task, return_exceptions=True)

    async def _handle_connection(self, websocket: websockets.ServerConnection):
        headers = dict(websocket.request.headers)
        if headers.get("device-id", None) is None:
            from urllib.parse import parse_qs, urlparse

            request_path = websocket.request.path
            if not request_path:
                self.logger.bind(tag=TAG).error("无法获取请求路径")
                await websocket.close()
                return
            parsed_url = urlparse(request_path)
            query_params = parse_qs(parsed_url.query)
            if "device-id" not in query_params:
                await websocket.send("端口正常，如需测试连接，请使用test_page.html")
                await websocket.close()
                return
            else:
                websocket.request.headers["device-id"] = query_params["device-id"][0]
            if "client-id" in query_params:
                websocket.request.headers["client-id"] = query_params["client-id"][0]
            if "authorization" in query_params:
                websocket.request.headers["authorization"] = query_params["authorization"][0]

        try:
            await self._handle_auth(websocket)
        except AuthenticationError:
            await websocket.send("认证失败")
            await websocket.close()
            return
        connection_memory = self._create_memory_provider()
        handler = ConnectionHandler(
            self.config,
            self._vad,
            self._asr,
            self._llm,
            connection_memory,
            self._intent,
            self,
        )
        try:
            await handler.handle_connection(websocket)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"处理连接时出错: {e}")
        finally:
            try:
                if hasattr(websocket, "closed") and not websocket.closed:
                    await websocket.close()
                elif hasattr(websocket, "state") and websocket.state.name != "CLOSED":
                    await websocket.close()
                else:
                    await websocket.close()
            except Exception as close_error:
                self.logger.bind(tag=TAG).error(f"服务器端强制关闭连接时出错: {close_error}")

    def _create_memory_provider(self):
        """记忆 provider 含连接级状态，不能在多个设备连接间共享。"""
        selected = self.config.get("selected_module", {}).get("Memory")
        if not selected:
            return None
        memory_config = self.config.get("Memory", {}).get(selected, {})
        memory_type = memory_config.get("type", selected)
        return memory_utils.create_instance(
            memory_type, memory_config, self.config.get("summaryMemory")
        )

    async def _http_response(self, websocket, request_headers):
        if request_headers.headers.get("connection", "").lower() == "upgrade":
            return None
        else:
            return websocket.respond(200, "Server is running\n")

    async def _handle_auth(self, websocket: websockets.ServerConnection):
        if self.auth_enable:
            headers = dict(websocket.request.headers)
            device_id = headers.get("device-id", None)
            client_id = headers.get("client-id", None)
            if self.allowed_devices and device_id in self.allowed_devices:
                return
            else:
                token = headers.get("authorization", "")
                if token.startswith("Bearer "):
                    token = token[7:]
                else:
                    raise AuthenticationError("Missing or invalid Authorization header")
                auth_success = self.auth.verify_token(
                    token, client_id=client_id, username=device_id
                )
                if not auth_success:
                    raise AuthenticationError("Invalid token")
