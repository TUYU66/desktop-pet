from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.transport.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

handle_exit_intent_function_desc = {
    "type": "function",
    "function": {
        "name": "handle_exit_intent",
        "description": "仅当用户明确要求待命、停止聆听或结束对话时调用。切歌、搜索音乐、设置或修改日程完成不代表结束对话。",
        "parameters": {
            "type": "object",
            "properties": {
                "say_goodbye": {
                    "type": "string",
                    "description": "和用户友好结束对话的告别语",
                }
            },
            "required": ["say_goodbye"],
        },
    },
}


@register_function(
    "handle_exit_intent", handle_exit_intent_function_desc, ToolType.SYSTEM_CTL
)
async def handle_exit_intent(conn: "ConnectionHandler", say_goodbye: str | None = None):
    # 处理退出意图
    try:
        from core.conversation.standby import request_standby, exit_requested
        if not exit_requested(getattr(conn, 'latest_user_text', ''), conn.config.get('customWakeWord', '')):
            return ActionResponse(
                action=Action.REQLLM, result='用户未明确要求结束对话，不执行待命；请继续处理用户原本的请求。')
        await request_standby(conn)
        logger.bind(tag=TAG).info("退出意图已排队确认语音，播完后待命")
        return ActionResponse(
            action=Action.NONE, result="待命确认已排队", response=""
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"处理退出意图错误: {e}")
        return ActionResponse(
            action=Action.NONE, result="退出意图处理失败", response=""
        )
