from ..base import IntentProviderBase
from typing import List, Dict
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


class IntentProvider(IntentProviderBase):
    async def detect_intent(self, conn, dialogue_history: List[Dict], text: str) -> str:
        """
        工具调用模式适配器：由聊天模型直接选择工具，不另跑一次意图识别。
        handle_user_intent 在此模式下直接进入聊天引擎；此返回值仅满足统一接口。
        Args:
            dialogue_history: 对话历史记录列表
            text: 本次对话记录
        Returns:
            固定返回"继续聊天"
        """
        logger.bind(tag=TAG).debug(
            "Using functionCallProvider, always returning continue chat"
        )
        return '{"function_call": {"name": "continue_chat"}}'
