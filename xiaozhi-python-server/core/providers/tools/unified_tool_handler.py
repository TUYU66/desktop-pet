"""统一工具处理器"""

import json
from typing import Dict, List, Any, Optional
from config.logger import setup_logging
from plugins_func.loadplugins import auto_import_modules

from .base import ToolType
from plugins_func.register import Action, ActionResponse
from .unified_tool_manager import ToolManager
from .server_plugins import ServerPluginExecutor
from .server_mcp import ServerMCPExecutor
from .device_iot import DeviceIoTExecutor
from .device_mcp import DeviceMCPExecutor
from core.handle.sendAudioHandle import send_display_message


class UnifiedToolHandler:
    """统一工具处理器"""

    def __init__(self, conn):
        self.conn = conn
        self.config = conn.config
        self.logger = setup_logging()

        # 创建工具管理器
        self.tool_manager = ToolManager(conn)

        # 创建各类执行器
        self.server_plugin_executor = ServerPluginExecutor(conn)
        self.server_mcp_executor = ServerMCPExecutor(conn)
        self.device_iot_executor = DeviceIoTExecutor(conn)
        self.device_mcp_executor = DeviceMCPExecutor(conn)

        # 注册执行器
        self.tool_manager.register_executor(
            ToolType.SERVER_PLUGIN, self.server_plugin_executor
        )
        self.tool_manager.register_executor(
            ToolType.SERVER_MCP, self.server_mcp_executor
        )
        self.tool_manager.register_executor(
            ToolType.DEVICE_IOT, self.device_iot_executor
        )
        self.tool_manager.register_executor(
            ToolType.DEVICE_MCP, self.device_mcp_executor
        )

        # 初始化标志
        self.finish_init = False

    async def _initialize(self):
        """异步初始化"""
        try:
            # 自动导入插件模块
            auto_import_modules("plugins_func.functions")

            # 初始化服务端MCP
            await self.server_mcp_executor.initialize()

            # 初始化Home Assistant（如果需要）
            self._initialize_home_assistant()

            self.finish_init = True
            self.logger.debug("统一工具处理器初始化完成")

            # 输出当前支持的所有工具列表
            self.current_support_functions()

        except Exception as e:
            self.logger.error(f"统一工具处理器初始化失败: {e}")

    def _initialize_home_assistant(self):
        """初始化Home Assistant提示词"""
        try:
            from plugins_func.functions.hass_init import append_devices_to_prompt

            append_devices_to_prompt(self.conn)
        except ImportError:
            pass  # 忽略导入错误
        except Exception as e:
            self.logger.error(f"初始化Home Assistant失败: {e}")

    def get_functions(self) -> List[Dict[str, Any]]:
        """获取所有工具的函数描述"""
        return self.tool_manager.get_function_descriptions()

    def current_support_functions(self) -> List[str]:
        """获取当前支持的函数名称列表"""
        func_names = self.tool_manager.get_supported_tool_names()
        self.logger.info(f"当前支持的函数列表: {func_names}")
        return func_names

    def upload_functions_desc(self):
        """刷新函数描述列表"""
        self.tool_manager.refresh_tools()
        self.logger.info("函数描述列表已刷新")

    def has_tool(self, tool_name: str) -> bool:
        """检查是否有指定工具"""
        return self.tool_manager.has_tool(tool_name)

    async def handle_llm_function_call(
        self, conn, function_call_data: Dict[str, Any]
    ) -> Optional[ActionResponse]:
        """处理LLM函数调用"""
        try:
            # 处理多函数调用
            if "function_calls" in function_call_data:
                calls = function_call_data["function_calls"]
                if any(call.get("name") in {"self_chassis_stand_up", "self_chassis_rest", "self_chassis_turn_left", "self_chassis_turn_right", "self_chassis_turn_around"} for call in calls):
                    if len(calls) != 1:
                        return ActionResponse(
                            action=Action.ERROR,
                            response="一次只能做一个身体动作，你想让我先做哪个？",
                        )
                    return await self.handle_llm_function_call(conn, calls[0])
                responses = []
                for call in calls:
                    result = await self.tool_manager.execute_tool(
                        call["name"], call.get("arguments", {})
                    )
                    responses.append(result)
                return self._combine_responses(responses)

            # 处理单函数调用
            function_name = function_call_data["name"]
            arguments = function_call_data.get("arguments", {})

            # 如果arguments是字符串，尝试解析为JSON
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments else {}
                except json.JSONDecodeError:
                    self.logger.error(f"无法解析函数参数: {arguments}")
                    return ActionResponse(
                        action=Action.ERROR,
                        response="这次没弄清该怎么操作，你再告诉我想做什么吧。",
                    )

            self.logger.debug(f"调用函数: {function_name}, 参数: {arguments}")

            # 发送工具调用显示消息到设备
            try:
                await send_display_message(self.conn, f"% {function_name}")
            except Exception as e:
                self.logger.warning(f"发送工具调用显示消息失败: {e}")

            if function_name in {"self_chassis_stand_up", "self_chassis_rest", "self_chassis_turn_left", "self_chassis_turn_right", "self_chassis_turn_around"}:
                mcp_client = getattr(conn, "mcp_client", None)
                if (
                    mcp_client is None
                    or not await mcp_client.is_ready()
                    or not self.tool_manager.has_tool(function_name)
                ):
                    return ActionResponse(
                        action=Action.ERROR,
                        response="我还没连接好，暂时动不了。",
                    )
                if conn.pending_chassis_action is not None:
                    return ActionResponse(
                        action=Action.ERROR,
                        response="刚才那个动作还没做完，等一下再告诉我吧。",
                    )
                conn.pending_chassis_action = {
                    "sentence_id": conn.sentence_id,
                    "name": function_name,
                    "arguments": arguments,
                    "audio_sent": False,
                }
                from core.conversation.feedback import chassis_acknowledgement
                response=chassis_acknowledgement(function_name,getattr(conn,'latest_user_text',''))
                self.logger.info(f"动作已排队，等播报结束再执行: {function_name}")
                return ActionResponse(action=Action.RESPONSE, response=response)

            # 执行工具调用
            result = await self.tool_manager.execute_tool(function_name, arguments)
            return result

        except Exception as e:
            self.logger.error(f"处理function call错误: {e}")
            return ActionResponse(action=Action.ERROR, result=str(e), response="这次有没有处理好，我还没确认，你先在网页看一下吧。")

    def _combine_responses(self, responses: List[ActionResponse]) -> ActionResponse:
        """合并多个函数调用的响应"""
        if not responses:
            return ActionResponse(action=Action.NONE, response="无响应")

        # 如果有任何错误，返回第一个错误
        for response in responses:
            if response.action == Action.ERROR:
                return response

        # 合并所有成功的响应
        contents = []
        responses_text = []

        for response in responses:
            if response.content:
                contents.append(response.content)
            if response.response:
                responses_text.append(response.response)

        # 确定最终的动作类型
        final_action = Action.RESPONSE
        for response in responses:
            if response.action == Action.REQLLM:
                final_action = Action.REQLLM
                break

        return ActionResponse(
            action=final_action,
            result="; ".join(contents) if contents else None,
            response="; ".join(responses_text) if responses_text else None,
        )

    async def register_iot_tools(self, descriptors: List[Dict[str, Any]]):
        """注册IoT设备工具"""
        self.device_iot_executor.register_iot_tools(descriptors)
        self.tool_manager.refresh_tools()
        self.logger.info(f"注册了{len(descriptors)}个IoT设备的工具")

    def get_tool_statistics(self) -> Dict[str, int]:
        """获取工具统计信息"""
        return self.tool_manager.get_tool_statistics()

    async def cleanup(self):
        """清理资源"""
        try:
            await self.server_mcp_executor.cleanup()

            self.logger.info("工具处理器清理完成")
        except Exception as e:
            self.logger.error(f"工具处理器清理失败: {e}")
