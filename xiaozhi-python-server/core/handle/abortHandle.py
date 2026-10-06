import json
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.transport.connection import ConnectionHandler
TAG = __name__


async def handleAbortMessage(conn: "ConnectionHandler", *, standby=False):
    conn.logger.bind(tag=TAG).info("Abort message received")
    standby_sentence = getattr(conn, 'standby_after_sentence', None) if standby else None
    if not standby and getattr(conn, 'standby_after_sentence', None):
        from core.conversation.standby import cancel_standby_reply
        cancel_standby_reply(conn)
    # 设置成打断状态，会自动打断llm、tts任务
    conn.close_after_chat = False
    from core.conversation.standby import conversation_awake, supports_standby, listening_allowed
    conn.return_to_standby = supports_standby(conn) and not conversation_awake(conn)
    conn.pending_chassis_action = None
    conn.client_abort = True
    from core.conversation.cancellation import cancel_active_chat
    cancel_active_chat(conn)
    conn.input_generation = getattr(conn, 'input_generation', 0) + 1
    catchup = getattr(conn, 'task_followup_audio_task', None)
    if catchup and not catchup.done():
        catchup.cancel()
    for attr in ('task_followup_task', 'reminder_delivery_task'):
        task = getattr(conn, attr, None)
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
    conn.clear_queues()
    # 打断客户端说话状态
    await conn.websocket.send(
        json.dumps({"type": "tts", "state": "stop", "session_id": conn.session_id,
                    "aborted": True, "listen_after": listening_allowed(conn) and not standby})
    )
    conn.clearSpeakStatus()
    if standby and (standby_sentence is None
                    or getattr(conn, 'standby_after_sentence', None) == standby_sentence):
        from core.conversation.standby import enter_standby
        await enter_standby(conn, force=True)
    # Stop local output before waiting for music cleanup or a reminder service ACK.
    from core.music import pause_for_chat
    await pause_for_chat(conn)
    from core.reminders.music import interrupt_music
    await interrupt_music(conn)
    conn.logger.bind(tag=TAG).info("Abort message received-end")
