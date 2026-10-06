import time
import asyncio
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.transport.connection import ConnectionHandler

from core.handle.receiveAudioHandle import startToChat
from core.handle.reportHandle import enqueue_asr_report
from core.handle.sendAudioHandle import send_stt_message, send_tts_message
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.utils.util import remove_punctuation_and_length
from core.providers.asr.dto.dto import InterfaceType

TAG = __name__

class ListenTextMessageHandler(TextMessageHandler):
    """Listen消息处理器"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.LISTEN

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        from core.conversation.standby import supports_standby, begin_interaction, conversation_awake
        state = msg_json.get('state')
        if state == 'standby' and msg_json.get('requestId'):
            if msg_json['requestId'] == getattr(conn, 'standby_request_id', None):
                conn.standby_request_id = None
                conn.logger.bind(tag=TAG).info('设备已确认待命')
            return
        if getattr(conn, 'explicit_standby', False):
            if state == 'detect':
                conn.explicit_standby = False
                conn.standby_request_id = None
            elif state == 'start':
                return  # Late automatic listening from the cancelled reply.
        current_music = getattr(conn, 'local_music', None)
        music_active = current_music and current_music.state in ('loading', 'playing')
        if msg_json.get('state') == 'detect':
            # Some device states send detect after abort for the same wake.
            # Consume that notification without starting the normal greeting.
            if (getattr(conn, 'music_wake_prompt_until', 0) > time.monotonic()
                    and getattr(conn, 'music_wake_prompt_sentence', None) == conn.sentence_id):
                return
            if music_active:
                from core.handle.abortHandle import handleAbortMessage
                from core.music import prompt_music_control
                await handleAbortMessage(conn)
                await prompt_music_control(conn)
                return
        # TTS stop makes the device acknowledge automatic listening. This is
        # not a user interruption, even if it arrives after music has started.
        # Real wake interruptions arrive as abort(wake_word_detected), which
        # pauses music before listen:start; detect and manual starts stay active.
        if music_active and (msg_json.get('state') == 'standby' or
                             (msg_json.get('state') == 'start' and msg_json.get('mode') != 'manual')):
            conn.logger.bind(tag=TAG).debug('音乐{}期间忽略自动聆听/待命回执', current_music.state)
            return
        if msg_json.get("state") == "standby" and supports_standby(conn):
            conn.standby = True
            conn.conversation_awake = False
            conn.close_after_chat = False
            conn.reset_audio_states()
            return
        if (state == 'start' and msg_json.get('mode') != 'manual'
                and (getattr(conn, 'return_to_standby', False)
                     or (conn.client_is_speaking and not conversation_awake(conn)))):
            return  # Full-duplex TTS acknowledgement is not a new user wake.
        if state == 'start' and supports_standby(conn) and not conversation_awake(conn):
            return  # A start receipt, including manual mode, cannot wake the robot.
        if msg_json.get("state") in ("start", "detect"):
            from core.music import pause_for_chat
            begin_interaction(conn, awake=state == 'detect')
            await pause_for_chat(conn)
        if "mode" in msg_json:
            conn.client_listen_mode = msg_json["mode"]
            conn.logger.bind(tag=TAG).debug(
                f"客户端拾音模式：{conn.client_listen_mode}"
            )
        if msg_json["state"] == "start":
            # 设备从播放模式切回录音模式,清除所有音频状态和缓冲区
            conn.reset_audio_states()
        elif msg_json["state"] == "stop":
            conn.client_voice_stop = True
            if conn.asr.interface_type == InterfaceType.STREAM:
                # 流式模式下，发送结束请求
                asyncio.create_task(conn.asr._send_stop_request())
            else:
                # 非流式模式：直接触发ASR识别
                if len(conn.asr_audio) > 0:
                    asr_audio_task = conn.asr_audio.copy()
                    conn.reset_audio_states()

                    if len(asr_audio_task) > 0:
                        conn.asr.queue_utterance(conn, asr_audio_task)
        elif msg_json["state"] == "detect":
            conn.client_have_voice = False
            conn.reset_audio_states()
            if "text" in msg_json:
                conn.last_activity_time = time.time() * 1000
                original_text = msg_json["text"]  # 保留原始文本
                filtered_len, filtered_text = remove_punctuation_and_length(
                    original_text
                )

                # 识别是否是唤醒词
                is_wakeup_words = (filtered_text in conn.config.get("wakeup_words", [])
                                   or filtered_text == "你好小智"
                                   or filtered_text == conn.config.get("customWakeWord"))
                # 是否开启唤醒词回复
                enable_greeting = conn.config.get("enable_greeting", True)

                if is_wakeup_words and not enable_greeting:
                    # 如果是唤醒词，且关闭了唤醒词回复，就不用回答
                    await send_stt_message(conn, original_text)
                    await send_tts_message(conn, "stop", None)
                    conn.client_is_speaking = False
                    from core.reminders.followup import queue_on_wake
                    queue_on_wake(conn)
                elif is_wakeup_words:
                    conn.just_woken_up = True
                    # 上报用户的唤醒词（不是回复内容）
                    enqueue_asr_report(conn, original_text, [])
                    await startToChat(conn, "嘿，你好呀", display_text=original_text)
                else:
                    conn.just_woken_up = True
                    # 上报纯文字数据（复用ASR上报功能，但不提供音频数据）
                    enqueue_asr_report(conn, original_text, [])
                    # 否则需要LLM对文字内容进行答复
                    await startToChat(conn, original_text)
