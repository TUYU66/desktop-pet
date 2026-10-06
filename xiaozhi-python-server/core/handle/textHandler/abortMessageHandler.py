from typing import Dict, Any

from core.handle.abortHandle import handleAbortMessage
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType


class AbortTextMessageHandler(TextMessageHandler):
    """Abort消息处理器"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.ABORT

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        reason = msg_json.get('reason')
        reason = reason if reason in ('wake_word_detected', 'speech_detected') else 'unspecified'
        conn.logger.bind(tag=__name__).info('设备请求打断：reason={}', reason)
        from core.conversation.standby import begin_interaction, supports_standby, conversation_awake
        if reason == 'wake_word_detected':
            begin_interaction(conn, awake=True)
        elif reason == 'speech_detected' and supports_standby(conn) and not conversation_awake(conn):
            return  # Speech/echo during a web reply is not a wake-word event.
        current = getattr(conn, 'local_music', None)
        music_wake = (msg_json.get('reason') == 'wake_word_detected'
                      and current and current.state in ('playing', 'loading'))
        await handleAbortMessage(conn)
        if music_wake:
            # The wake reason itself is sufficient. A following listen:start
            # is only a device acknowledgement, and may not arrive at all.
            from core.music import prompt_music_control
            await prompt_music_control(conn)
