import json
import time
from typing import Dict, Any

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.device.health import connection_services

TAG = __name__


class PingMessageHandler(TextMessageHandler):
    """Ping消息处理器，用于保持WebSocket连接"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.PING

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        """
        处理PING消息，发送PONG响应
        消息格式：{"type": "ping"}
        Args:
            conn: WebSocket连接对象
            msg_json: PING消息的JSON数据
        """
        # 检查是否启用了WebSocket心跳功能
        enable_websocket_ping = conn.config.get("enable_websocket_ping", False)
        from core.conversation.standby import supports_standby
        persistent = supports_standby(conn)
        health_supported = (getattr(conn, "features", None) or {}).get("service_status", False)
        if not enable_websocket_ping and not persistent and not health_supported:
            conn.logger.debug(f"WebSocket心跳功能未启用，忽略PING消息")
            return

        try:
            conn.logger.debug(f"收到PING消息，发送PONG响应")
            conn.last_transport_time = time.time() * 1000
            if not persistent:
                conn.last_activity_time = conn.last_transport_time
            # 构造PONG响应消息
            pong_message = {
                "type": "pong",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            }
            if health_supported:
                pong_message["services"] = connection_services(conn)

            # 发送PONG响应
            await conn.websocket.send(json.dumps(pong_message))

        except Exception as e:
            conn.logger.error(f"处理PING消息时发生错误: {e}")
