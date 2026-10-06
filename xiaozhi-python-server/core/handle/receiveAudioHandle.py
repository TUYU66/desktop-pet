import time
import json
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.transport.connection import ConnectionHandler
from core.utils.util import audio_to_data
from core.handle.abortHandle import handleAbortMessage
from core.handle.intentHandler import handle_user_intent
from core.utils.output_counter import check_device_output_limit
from core.handle.sendAudioHandle import send_stt_message, SentenceType

TAG = __name__


async def handleAudioMessage(conn: "ConnectionHandler", audio):
    from core.conversation.standby import conversation_awake, supports_standby
    if supports_standby(conn) and not conversation_awake(conn):
        return  # Web/reminder speech never grants microphone access.
    music = getattr(conn, 'local_music', None)
    if music and music.state == 'playing':
        return  # Music standby accepts wake/control messages, not microphone echo.
    if getattr(conn, "standby", False):
        return
    # 当前片段是否有人说话
    have_voice = conn.vad.is_vad(conn, audio)
    # 如果设备刚刚被唤醒，短暂忽略VAD检测（不丢弃音频，只是标记无语音）
    if (getattr(conn, "just_woken_up", False)
            and not (conn.features or {}).get('voice_barge_in')):
        have_voice = False
        # 设置一个短暂延迟后恢复VAD检测
        if not hasattr(conn, "vad_resume_task") or conn.vad_resume_task.done():
            conn.vad_resume_task = asyncio.create_task(resume_vad_detection(conn))
        return
    # 设备长时间空闲检测，用于say goodbye
    await no_voice_close_connect(conn, have_voice)
    if getattr(conn, 'standby', False):
        return
    if have_voice:
        conn.last_conversation_activity = time.monotonic()
        # A gated firmware owns interruption decisions and sends abort after
        # echo rejection. Do not override it with a second raw VAD decision.
        if (conn.client_listen_mode == 'realtime'
                and (conn.features or {}).get('voice_barge_in')
                and not (conn.features or {}).get('voice_barge_in_gated')
                and (conn.client_is_speaking or conn.chat_lock.locked())
                and not conn.client_abort):
            await handleAbortMessage(conn)
    # 接收音频
    await conn.asr.receive_audio(conn, audio, have_voice)


async def resume_vad_detection(conn: "ConnectionHandler"):
    # 等待2秒后恢复VAD检测
    await asyncio.sleep(2)
    conn.just_woken_up = False


async def startToChat(conn: "ConnectionHandler", text, display_text=None):
    # 检查输入是否是JSON格式（包含说话人信息）
    speaker_name = None
    actual_text = text

    try:
        # 尝试解析JSON格式的输入
        if text.strip().startswith("{") and text.strip().endswith("}"):
            data = json.loads(text)
            if "speaker" in data and "content" in data:
                speaker_name = data["speaker"]
                actual_text = data["content"]
                conn.logger.bind(tag=TAG).info(f"解析到说话人信息: {speaker_name}")
            elif "content" in data:
                actual_text = data["content"]
    except (json.JSONDecodeError, KeyError):
        # 如果解析失败，继续使用原始文本
        pass

    # 保存说话人信息到连接对象
    if speaker_name:
        conn.current_speaker = speaker_name
    else:
        conn.current_speaker = None
    from core.conversation.standby import begin_interaction, supports_standby, conversation_awake
    if supports_standby(conn) and not conversation_awake(conn):
        return  # Late ASR work from the previous voice conversation.
    if getattr(conn, '_idle_standby_reply', False):
        from core.conversation.standby import cancel_standby_reply
        cancel_standby_reply(conn)
    begin_interaction(conn)

    # Display once, before any feature is allowed to consume the utterance.
    await send_stt_message(conn, actual_text if display_text is None else display_text)
    from core.conversation.standby import exit_requested, request_standby, stop_speaking_requested
    if exit_requested(actual_text, conn.config.get('customWakeWord', '')):
        await request_standby(conn)
        return
    if stop_speaking_requested(actual_text):
        await handleAbortMessage(conn)
        return

    # 如果当日的输出字数大于限定的字数
    if conn.max_output_size > 0:
        if check_device_output_limit(
            conn.headers.get("device-id"), conn.max_output_size
        ):
            await max_out_size(conn)
            return

    from core.music.conversation import handle_input
    handled, actual_text = await handle_input(conn, actual_text)
    if handled:
        return

    # manual 模式下不打断正在播放的内容
    if (conn.client_is_speaking or conn.chat_lock.locked()) and conn.client_listen_mode != "manual":
        await handleAbortMessage(conn)

    # 首先进行意图分析，使用实际文本内容
    from core.reminders.conversation import relevant, expects_reply
    from core.conversation.standby import exit_requested
    intent_handled = False if exit_requested(actual_text) or relevant(actual_text) or getattr(conn, 'schedule_pending', None) or getattr(conn, 'schedule_batch_pending', None) or getattr(conn, 'schedule_edit_pending', None) or getattr(conn, 'schedule_control_pending', None) or expects_reply(conn) else await handle_user_intent(conn, actual_text)

    if intent_handled:
        # 如果意图已被处理，不再进行聊天
        return

    # 意图未被处理，继续常规聊天流程，使用实际文本内容
    # 准备开始新会话
    conn.executor.submit(conn.chat, actual_text, generation=getattr(conn, 'input_generation', 0))


async def no_voice_close_connect(conn: "ConnectionHandler", have_voice):
    from core.conversation.standby import supports_standby, maybe_idle_standby
    if supports_standby(conn):
        if have_voice: conn.last_activity_time = time.time()*1000
        await maybe_idle_standby(conn)
        return
    if have_voice:
        conn.last_activity_time = time.time() * 1000
        return
    # 只有在已经初始化过时间戳的情况下才进行超时检查
    if conn.last_activity_time > 0.0:
        no_voice_time = time.time() * 1000 - conn.last_activity_time
        close_connection_no_voice_time = int(
            conn.config.get("close_connection_no_voice_time", 120)
        )
        if (
            not conn.close_after_chat
            and no_voice_time > 1000 * close_connection_no_voice_time
        ):
            conn.close_after_chat = True
            conn.client_abort = False
            end_prompt = conn.config.get("end_prompt", {})
            if end_prompt and end_prompt.get("enable", True) is False:
                conn.logger.bind(tag=TAG).info("结束对话，无需发送结束提示语")
                from core.conversation.standby import enter_standby
                await enter_standby(conn)
                return
            prompt = end_prompt.get("prompt")
            if not prompt:
                prompt = "请你以```时间过得真快```未来头，用富有感情、依依不舍的话来结束这场对话吧。！"
            await startToChat(conn, prompt)


async def max_out_size(conn: "ConnectionHandler"):
    # 播放超出最大输出字数的提示
    conn.client_abort = False
    text = "不好意思，我现在有点事情要忙，明天这个时候我们再聊，约好了哦！明天不见不散，拜拜！"
    await send_stt_message(conn, text)
    file_path = "config/assets/max_output_size.wav"
    opus_packets = await audio_to_data(file_path)
    conn.tts.tts_audio_queue.put((SentenceType.LAST, opus_packets, text))
    conn.close_after_chat = True
    conn.tts.tts_audio_queue.put((SentenceType.LAST, [], None))
