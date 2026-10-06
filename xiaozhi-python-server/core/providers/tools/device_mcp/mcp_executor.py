"""设备端MCP工具执行器"""

from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.transport.connection import ConnectionHandler
from ..base import ToolType, ToolDefinition, ToolExecutor
from plugins_func.register import Action, ActionResponse
from .mcp_handler import call_mcp_tool
from core.device.chassis_intent import TURN_TOOL_NAMES
from core.conversation.requests import action_progress, unconfirmed_action_result

CHASSIS_TOOLS = TURN_TOOL_NAMES | {"self_chassis_stand_up", "self_chassis_rest",
                                 "self_chassis_bluetooth_on", "self_chassis_bluetooth_off"}


class DeviceMCPExecutor(ToolExecutor):
    """设备端MCP工具执行器"""

    def __init__(self, conn):
        self.conn = conn

    async def execute(
        self, conn: "ConnectionHandler", tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        """执行设备端MCP工具"""
        if not hasattr(conn, "mcp_client") or not conn.mcp_client:
            return ActionResponse(
                action=Action.ERROR,
                result="设备端MCP客户端未初始化",
                response="设备还没连好，连好后再试吧。",
            )

        if not await conn.mcp_client.is_ready():
            return ActionResponse(
                action=Action.ERROR,
                result="设备端MCP客户端未准备就绪",
                response="设备还没准备好，等一下再试吧。",
            )

        chassis = tool_name in CHASSIS_TOOLS
        sentence = getattr(conn, 'sentence_id', None)
        outcome = ''
        if chassis:
            action_progress(conn, sentence, waiting=True)
        try:
            # 转换参数为JSON字符串
            import json

            args_str = json.dumps(arguments) if arguments else "{}"

            # 调用设备端MCP工具
            # The STM32 confirms the complete stand/turn/rest sequence, not
            # merely its initial ACK. Allow all physical phases to finish.
            result = await call_mcp_tool(
                conn, conn.mcp_client, tool_name, args_str,
                timeout=60 if tool_name in TURN_TOOL_NAMES else 30,
            )
            outcome = str(result)

            resultJson = None
            if isinstance(result, str):
                try:
                    resultJson = json.loads(result)
                except Exception as e:
                    pass

            # 视觉大模型不经过二次LLM处理
            if (
                resultJson is not None
                and isinstance(resultJson, dict)
                and "action" in resultJson
            ):
                return ActionResponse(
                    action=Action[resultJson["action"]],
                    response=resultJson.get("response", ""),
                )

            # Chassis actions already return a short first-person sentence (or
            # an explicit failure). Speak it once instead of asking the LLM to
            # rephrase it and append speculative progress updates.
            if chassis:
                from core.conversation.feedback import chassis_outcome
                return ActionResponse(
                    action=Action.RESPONSE, result=str(result), response=chassis_outcome(tool_name,str(result))
                )

            return ActionResponse(action=Action.REQLLM, result=str(result))

        except ValueError as e:
            return ActionResponse(action=Action.NOTFOUND, result=str(e), response="这次没找到对应的设备功能，你在网页检查一下连接吧。")
        except Exception as e:
            outcome = str(e)
            return ActionResponse(action=Action.ERROR, result=str(e), response="这次有没有做好，我还没确认，你先看一下设备的状态。")
        finally:
            if chassis:
                action_progress(conn, sentence, waiting=False,
                                unconfirmed=unconfirmed_action_result(outcome))

    def get_tools(self) -> Dict[str, ToolDefinition]:
        """获取所有设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return {}

        tools = {}
        mcp_tools = self.conn.mcp_client.get_available_tools()

        for tool in mcp_tools:
            func_def = tool.get("function", {})
            tool_name = func_def.get("name", "")

            if tool_name:
                tools[tool_name] = ToolDefinition(
                    name=tool_name, description=tool, tool_type=ToolType.DEVICE_MCP
                )

        return tools

    def has_tool(self, tool_name: str) -> bool:
        """检查是否有指定的设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return False

        return self.conn.mcp_client.has_tool(tool_name)
