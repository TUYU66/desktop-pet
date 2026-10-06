import json
import time
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.transport.connection import ConnectionHandler
from core.utils import textUtils
from core.utils.util import audio_to_data
from core.providers.tts.dto.dto import SentenceType
from core.utils.audioRateController import AudioRateController

TAG = __name__
# 音频帧时长（毫秒）
AUDIO_FRAME_DURATION = 60
# 预缓冲包数量，直接发送以减少延迟
PRE_BUFFER_COUNT = 5


async def sendAudioMessage(conn: "ConnectionHandler", sentenceType, audios, text, sentence_id=None):
    sentence_id = sentence_id or conn.sentence_id
    # 跳过旧句子残留音频
    if sentence_id is not None and sentence_id != conn.sentence_id:
        return

    if conn.tts.tts_audio_first_sentence:
        # STT's early start can precede allocation of the real response ID.
        # Bind the stream before its first subtitle/audio, using this response's ID.
        await send_tts_message(conn, "start", stream_id=sentence_id)
        conn.logger.bind(tag=TAG).info(f"发送第一段语音: {text}")
        conn.tts.tts_audio_first_sentence = False

    if sentenceType == SentenceType.FIRST:
        # 同一句子的后续消息加入流控队列，其他情况立即发送
        if (
            hasattr(conn, "audio_rate_controller")
            and conn.audio_rate_controller
            and getattr(conn, "audio_flow_control", {}).get("sentence_id")
            == conn.sentence_id
        ):
            conn.audio_rate_controller.add_message(
                lambda: send_tts_message(conn, "sentence_start", text, stream_id=sentence_id)
            )
        else:
            # 新句子或流控器未初始化，立即发送
            await send_tts_message(conn, "sentence_start", text, stream_id=sentence_id)

    await sendAudio(conn, audios)
    if audios:
        from core.conversation.requests import response_progress
        response_progress(conn, sentence_id)
    pending_chassis = getattr(conn, "pending_chassis_action", None)
    if pending_chassis and pending_chassis["sentence_id"] == sentence_id and audios:
        pending_chassis["audio_sent"] = True
    # 发送句子开始消息
    if sentenceType is not SentenceType.MIDDLE:
        conn.logger.bind(tag=TAG).info(f"发送音频消息: {sentenceType}, {text}")

    # 发送结束消息（如果是最后一个文本）
    if sentenceType == SentenceType.LAST:
        # Recheck this decision after audio drains: a real wake may cancel exit.
        should_standby = conn.close_after_chat or getattr(conn, "return_to_standby", False)
        # Keep music from starting between clearSpeakStatus and the final
        # stop/standby messages, which would otherwise stop the new stream.
        conn.tts_finishing = True
        finisher = asyncio.current_task()
        conn.tts_finishing_task = finisher
        finishing_generation = getattr(conn, 'input_generation', 0)
        try:
            from core.reminders.followup import deliver as deliver_task_followup
            await deliver_task_followup(conn, sentence_id)
            if (conn.client_abort or conn.sentence_id != sentence_id or conn.stop_event.is_set()
                    or getattr(conn, 'input_generation', 0) != finishing_generation):
                return  # An interrupted optional notice cannot stop a newer reply.
            await send_tts_message(conn, "stop", None, stream_id=sentence_id)
            pending_chassis = getattr(conn, "pending_chassis_action", None)
            if pending_chassis and pending_chassis["sentence_id"] == sentence_id:
                conn.pending_chassis_action = None
                if pending_chassis["audio_sent"] and not conn.client_abort and not conn.stop_event.is_set():
                    if await _run_chassis_after_speech(conn, pending_chassis):
                        # The queued outcome has its own LAST. Do not enter
                        # standby or release music before that feedback finishes.
                        return
                else:
                    conn.logger.bind(tag=TAG).warning("动作播报未完成，已取消动作")
            if (should_standby and conn.sentence_id == sentence_id
                    and (conn.close_after_chat or getattr(conn, 'return_to_standby', False))):
                from core.conversation.standby import enter_standby
                await enter_standby(conn, force=getattr(conn, 'standby_after_sentence', None) == sentence_id)
            music = getattr(conn, 'local_music', None)
            if music:
                music.speech_finished(sentence_id)
            from core.conversation.requests import response_progress
            response_progress(conn, sentence_id, finished=True)
        finally:
            if getattr(conn, 'tts_finishing_task', None) is finisher:
                conn.tts_finishing = False
                conn.tts_finishing_task = None


def _queue_chassis_outcome(conn, pending, text):
    """Keep the actual device outcome in speech, frontend TTS and dialogue."""
    if conn.client_abort or conn.stop_event.is_set() or conn.sentence_id != pending["sentence_id"]:
        return False
    from core.providers.tts.dto.dto import TTSMessageDTO, ContentType
    from core.utils.dialogue import Message
    conn.dialogue.put(Message(role="assistant", content=text))
    conn.tts.store_tts_text(pending["sentence_id"], text)
    for kind, content in ((SentenceType.FIRST, None),
                          (SentenceType.MIDDLE, text), (SentenceType.LAST, None)):
        conn.tts.tts_text_queue.put(TTSMessageDTO(
            sentence_id=pending["sentence_id"], sentence_type=kind,
            content_type=ContentType.TEXT if content else ContentType.ACTION,
            content_detail=content))
    return True


async def _confirm_chassis_for_music(conn, pending):
    """Bound the entire status check, not eight independent five-second calls."""
    from core.api.volume_handler import VolumeHandler
    expected_stopped = pending["name"].endswith("rest")

    async def check_status():
        confirmed = 0
        for _ in range(8):
            await asyncio.sleep(0.5)
            if conn.client_abort or conn.stop_event.is_set() or conn.sentence_id != pending["sentence_id"]:
                return False
            status = json.loads(await VolumeHandler()._call(conn, "self.chassis.get_status", {}))
            if conn.client_abort or conn.stop_event.is_set() or conn.sentence_id != pending["sentence_id"]:
                return False
            good = (status.get("connected") and status.get("statusValid")
                    and status.get("balanceStopped") is expected_stopped
                    and not status.get("lowBattery"))
            confirmed = confirmed + 1 if good else 0
            if confirmed >= 3:
                return True
        raise RuntimeError("未确认动作后的电机状态，已取消后续音乐")

    return await asyncio.wait_for(check_status(), timeout=6)


async def _run_chassis_after_speech(conn: "ConnectionHandler", pending: dict):
    """Execute a single chassis action only after its spoken reply was sent."""
    if conn.client_abort or conn.stop_event.is_set() or conn.sentence_id != pending["sentence_id"]:
        return
    from core.device.chassis_intent import TURN_TOOL_NAMES
    from core.conversation.requests import action_progress, unconfirmed_action_result
    action_progress(conn, pending['sentence_id'], waiting=True)
    try:
        result = await asyncio.wait_for(
            conn.func_handler.tool_manager.execute_tool(pending["name"], pending["arguments"]),
            timeout=65 if pending['name'] in TURN_TOOL_NAMES else 35,
        )
        conn.logger.bind(tag=TAG).info(
            f"播报后执行动作 {pending['name']}，设备结果: {result.result or result.response}"
        )
        raw_outcome = str(result.result or result.response or "没有收到明确的动作结果，请先查看我的状态。")
        action_progress(conn, pending['sentence_id'], waiting=False,
                        unconfirmed=unconfirmed_action_result(raw_outcome))
        outcome = str(result.response or raw_outcome)
        # Older firmware's acceptance-only responses must not imply completion.
        outcome = {"好的，我站起来啦。": "好，正在起身。",
                   "好的，我休息咯。": "好，正在往后靠。",
                   "这次没转好，我先不继续转了，帮我看一下姿态吧。":
                       "这次动作没能完整做完。"}.get(outcome, outcome)
        from core.handle.reportHandle import enqueue_tool_report
        enqueue_tool_report(conn, pending["name"], pending["arguments"], outcome, report_tool_call=False)
        completed_turns = {
            "self_chassis_turn_left": "好，我向左转过来啦。",
            "self_chassis_turn_right": "好，我向右转过来啦。",
            "self_chassis_turn_around": "好，我转过身来啦。",
        }
        if pending.get("next_action"):
            next_action = pending["next_action"]
            raw = result.result or result.response
            accepted_postures = {"self_chassis_stand_up": "起立完成。",
                                 "self_chassis_rest": "休息完成。"}
            expected = completed_turns.get(pending["name"]) or accepted_postures.get(pending["name"])
            if not expected or raw != expected:
                return _queue_chassis_outcome(conn, pending, outcome + "后面的动作先不做了。")
            from core.utils.dialogue import Message
            conn.dialogue.put(Message(role="assistant", content=outcome))
            if conn.client_abort or conn.stop_event.is_set() or conn.sentence_id != pending["sentence_id"]:
                return False
            # Let STM32 validate the next step, including an idempotent REST
            # when the preceding seated turn has already returned to support.
            conn.logger.bind(tag=TAG).info(f"顺序动作：前一步已确认，执行 {next_action}")
            enqueue_tool_report(conn, next_action, {})
            return await _run_chassis_after_speech(conn, {
                "sentence_id": pending["sentence_id"], "name": next_action, "arguments": {}})
        if pending.get("play_music_after"):
            # Firmware now returns explicit completion; fail closed on any
            # rejection/unknown response rather than treating isError=false as success.
            expected = {"self_chassis_stand_up": "起立完成。", "self_chassis_rest": "休息完成。"}.get(pending['name'])
            if not expected or (result.result or result.response) != expected:
                return _queue_chassis_outcome(conn, pending, outcome + "音乐也还没开始放。")
            from core.music import player
            if not await _confirm_chassis_for_music(conn, pending):
                return False
            music = player(conn)
            action = pending.get('music_action', 'play')
            if action == 'resume':
                target = pending.get('music_target')
                if not target:
                    return _queue_chassis_outcome(conn, pending, "动作做好了，不过还没有能接着放的歌，你先点一首吧。")
                if not music.track or (music.source, music.track['id']) != target:
                    return _queue_chassis_outcome(conn, pending, "动作做好了，不过歌曲已经换了，你还想接着听吗？")
            await music.command(action, after_sentence=pending["sentence_id"])
            conn.logger.bind(tag=TAG).info("组合指令：动作已接受且电机状态已核对，音乐已排队: action={}", action)
        from core.conversation.feedback import chassis_outcome
        return _queue_chassis_outcome(conn, pending, chassis_outcome(pending['name'],outcome))
    except Exception as exc:
        conn.logger.bind(tag=TAG).error(f"播报后执行动作 {pending['name']} 失败: {exc}")
        action_progress(conn, pending['sentence_id'], waiting=False, unconfirmed=True)
        message = "这次有没有做好，我还没确认，你先看一下我的状态。"
        if pending.get("play_music_after"):
            message += "音乐有没有接着放，我也还没确认。" if pending.get('music_action')=='resume' else "音乐有没有开始放，我也还没确认。"
        return _queue_chassis_outcome(conn, pending, message)


async def _wait_for_audio_completion(conn: "ConnectionHandler"):
    """
    等待音频队列清空并等待预缓冲包播放完成

    Args:
        conn: 连接对象
    """
    if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
        rate_controller = conn.audio_rate_controller
        conn.logger.bind(tag=TAG).debug(
            f"等待音频发送完成，队列中还有 {len(rate_controller.queue)} 个包"
        )
        # A failed sender never reaches check_queue's empty notification. Race
        # the drain against the sender itself so TTS LAST cannot wait forever.
        sender = getattr(rate_controller, 'pending_send_task', None)
        if sender is None:
            await rate_controller.queue_empty_event.wait()
        else:
            drained = asyncio.create_task(rate_controller.queue_empty_event.wait())
            try:
                await asyncio.wait({drained, sender}, return_when=asyncio.FIRST_COMPLETED)
                if rate_controller.pending_send_task is not sender:
                    raise asyncio.CancelledError('音频流已被替换')
                error = getattr(rate_controller, 'send_error', None)
                if error is not None:
                    raise ConnectionError('音频发送失败') from error
                if sender.done() and not rate_controller.queue_empty_event.is_set():
                    raise ConnectionError('音频发送任务已停止，队列未发送完成')
            finally:
                if not drained.done(): drained.cancel()
                await asyncio.gather(drained, return_exceptions=True)

        # 等待预缓冲包播放完成
        # 前N个包直接发送，增加2个网络抖动包，需要额外等待它们在客户端播放完成
        frame_duration_ms = rate_controller.frame_duration
        pre_buffer_playback_time = (PRE_BUFFER_COUNT + 2) * frame_duration_ms / 1000.0
        await asyncio.sleep(pre_buffer_playback_time)

        conn.logger.bind(tag=TAG).debug("音频发送完成")


async def _send_to_mqtt_gateway(
    conn: "ConnectionHandler", opus_packet, timestamp, sequence
):
    """
    发送带16字节头部的opus数据包给mqtt_gateway
    Args:
        conn: 连接对象
        opus_packet: opus数据包
        timestamp: 时间戳
        sequence: 序列号
    """
    # 为opus数据包添加16字节头部
    header = bytearray(16)
    header[0] = 1  # type
    header[2:4] = len(opus_packet).to_bytes(2, "big")  # payload length
    header[4:8] = sequence.to_bytes(4, "big")  # sequence
    header[8:12] = timestamp.to_bytes(4, "big")  # 时间戳
    header[12:16] = len(opus_packet).to_bytes(4, "big")  # opus长度

    # 发送包含头部的完整数据包
    complete_packet = bytes(header) + opus_packet
    await conn.websocket.send(complete_packet)


async def sendAudio(
    conn: "ConnectionHandler", audios, frame_duration=AUDIO_FRAME_DURATION
):
    """
    发送音频包，使用 AudioRateController 进行精确的流量控制

    Args:
        conn: 连接对象
        audios: 单个opus包(bytes) 或 opus包列表
        frame_duration: 帧时长（毫秒），默认使用全局常量AUDIO_FRAME_DURATION
    """
    if audios is None or len(audios) == 0:
        return

    send_delay = conn.config.get("tts_audio_send_delay", -1) / 1000.0
    is_single_packet = isinstance(audios, bytes)

    # 初始化或获取 RateController
    rate_controller, flow_control = _get_or_create_rate_controller(
        conn, frame_duration, is_single_packet
    )

    # 统一转换为列表处理
    audio_list = [audios] if is_single_packet else audios

    # 发送音频包
    await _send_audio_with_rate_control(
        conn, audio_list, rate_controller, flow_control, send_delay
    )


def _get_or_create_rate_controller(
    conn: "ConnectionHandler", frame_duration, is_single_packet
):
    """
    获取或创建 RateController 和 flow_control

    Args:
        conn: 连接对象
        frame_duration: 帧时长
        is_single_packet: 是否单包模式（True: TTS流式单包, False: 批量包）

    Returns:
        (rate_controller, flow_control)
    """
    # 检查是否需要重置控制器
    need_reset = False

    if not hasattr(conn, "audio_rate_controller"):
        # 控制器不存在，需要创建
        need_reset = True
    else:
        rate_controller = conn.audio_rate_controller

        if (getattr(conn, 'audio_flow_control', {}).get('sentence_id') == conn.sentence_id
                and getattr(rate_controller, 'send_error', None) is not None):
            # Do not hide a partially lost reply by restarting its sender. Only
            # an explicit reset or a new response may start a fresh audio stream.
            raise ConnectionError('本轮音频发送已失败') from rate_controller.send_error

        # 后台发送任务已停止, 则需要重置
        if (
            not rate_controller.pending_send_task
            or rate_controller.pending_send_task.done()
        ):
            need_reset = True
        # 当sentence_id 变化，需要重置
        elif (
            getattr(conn, "audio_flow_control", {}).get("sentence_id")
            != conn.sentence_id
        ):
            need_reset = True

    if need_reset:
        # 创建或获取 rate_controller
        if not hasattr(conn, "audio_rate_controller"):
            conn.audio_rate_controller = AudioRateController(frame_duration)
        else:
            conn.audio_rate_controller.reset()

        # 初始化 flow_control
        conn.audio_flow_control = {
            "packet_count": 0,
            "sequence": 0,
            "sentence_id": conn.sentence_id,
        }

        # 启动后台发送循环
        _start_background_sender(
            conn, conn.audio_rate_controller, conn.audio_flow_control
        )

    return conn.audio_rate_controller, conn.audio_flow_control


def _start_background_sender(conn: "ConnectionHandler", rate_controller, flow_control):
    """
    启动后台发送循环任务

    Args:
        conn: 连接对象
        rate_controller: 速率控制器
        flow_control: 流控状态
    """

    async def send_callback(packet):
        # 检查是否应该中止
        if conn.client_abort:
            raise asyncio.CancelledError("客户端已中止")

        conn.last_activity_time = time.time() * 1000
        await _do_send_audio(conn, packet, flow_control)

    # 使用 start_sending 启动后台循环
    rate_controller.start_sending(send_callback)


async def _send_audio_with_rate_control(
    conn: "ConnectionHandler", audio_list, rate_controller, flow_control, send_delay
):
    """
    使用 rate_controller 发送音频包

    Args:
        conn: 连接对象
        audio_list: 音频包列表
        rate_controller: 速率控制器
        flow_control: 流控状态
        send_delay: 固定延迟（秒），-1表示使用动态流控
    """
    for packet in audio_list:
        if conn.client_abort:
            return

        conn.last_activity_time = time.time() * 1000

        # 预缓冲：前N个包直接发送，快速填满 ESP32 音频缓冲区
        if flow_control["packet_count"] < PRE_BUFFER_COUNT:
            await _do_send_audio(conn, packet, flow_control)
        elif send_delay > 0:
            # 固定延迟模式
            await asyncio.sleep(send_delay)
            await _do_send_audio(conn, packet, flow_control)
        else:
            # 动态流控模式：仅添加到队列，由后台循环负责发送
            rate_controller.add_audio(packet)


async def _do_send_audio(conn: "ConnectionHandler", opus_packet, flow_control):
    """
    执行实际的音频发送
    """
    packet_index = flow_control.get("packet_count", 0)
    sequence = flow_control.get("sequence", 0)
    now=time.monotonic()
    buffered_until=flow_control.get('buffered_until',now)
    expected_gap=flow_control.pop('expected_gap',None)
    if packet_index and now-buffered_until>0.15:
        log=conn.logger.bind(tag=TAG).debug if expected_gap=='reminder_music_start' else conn.logger.bind(tag=TAG).warning
        log('音频发送供给间隔：sentence_id={}, estimated_gap_ms={}, packet_index={}, phase={}',
            flow_control.get('sentence_id'),round((now-buffered_until)*1000),packet_index,expected_gap or 'continuous_audio')
    flow_control['buffered_until']=max(now,buffered_until)+AUDIO_FRAME_DURATION/1000

    if conn.conn_from_mqtt_gateway:
        # 计算时间戳（基于播放位置）
        start_time = time.time()
        timestamp = int(start_time * 1000) % (2**32)
        await _send_to_mqtt_gateway(conn, opus_packet, timestamp, sequence)
    else:
        # 直接发送opus数据包
        await conn.websocket.send(opus_packet)

    # 更新流控状态
    flow_control["packet_count"] = packet_index + 1
    flow_control["sequence"] = sequence + 1


async def send_tts_message(conn: "ConnectionHandler", state, text=None, media=None, stream_id=None, music_title=None):
    """发送 TTS 状态消息"""
    if text is None and state == "sentence_start":
        return
    stream_id = stream_id or conn.sentence_id
    if stream_id != conn.sentence_id:
        return
    message = {"type": "tts", "state": state, "session_id": conn.session_id, "stream_id": stream_id}
    if media:
        message['media'] = media
    if media == 'music' and state == 'start' and isinstance(music_title, str):
        message['music_title'] = music_title
    if text is not None:
        message["text"] = textUtils.check_emoji(textUtils.normalize_spoken_text(text))

    # TTS播放结束
    if state == "stop":
        # 保存当前的 sentence_id，用于后续判断是否是当前轮次
        current_sentence_id = stream_id
        # 播放提示音
        tts_notify = conn.config.get("enable_stop_tts_notify", False)
        if tts_notify and media not in ('music', 'music_interrupted'):
            stop_tts_notify_voice = conn.config.get(
                "stop_tts_notify_voice", "config/assets/tts_notify.mp3"
            )
            audios = await audio_to_data(stop_tts_notify_voice, is_opus=True)
            if current_sentence_id != conn.sentence_id:
                return
            await sendAudio(conn, audios)

        # 只有句子未过期才等待音频发送完成（否则直接跳过）
        sentence_still_current = (current_sentence_id == conn.sentence_id)
        if sentence_still_current:
            try:
                await _wait_for_audio_completion(conn)
            except BaseException:
                if current_sentence_id == conn.sentence_id:
                    if getattr(conn, 'audio_rate_controller', None):
                        conn.audio_rate_controller.stop_sending()
                    conn.clearSpeakStatus()
                raise
            if current_sentence_id != conn.sentence_id:
                return
            if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
                conn.audio_rate_controller.stop_sending()

            # 等待最后一帧在 ESP32 端自然播放完毕，防 I2S DMA 断流爆音
            await asyncio.sleep(0.15)
        else:
            return

        # 无论如何都要清理说话状态，避免 client_is_speaking 永远卡在 True
        if current_sentence_id != conn.sentence_id:
            return
        conn.clearSpeakStatus()

    # 发送消息到客户端
    if state in ('start', 'stop'):
        # A fresh wake can cancel exit while the previous audio is draining.
        from core.conversation.standby import listening_allowed
        message['listen_after'] = (listening_allowed(conn)
            and not (state == 'stop' and getattr(conn, '_idle_standby_reply', False)
                     and getattr(conn, 'standby_after_sentence', None) == stream_id))
    if state == 'start':
        conn.client_is_speaking = True
    await conn.websocket.send(json.dumps(message))


async def send_stt_message(conn: "ConnectionHandler", text):
    """发送 STT 状态消息"""
    end_prompt_str = conn.config.get("end_prompt", {}).get("prompt")
    if end_prompt_str and end_prompt_str == text:
        await send_tts_message(conn, "start")
        return

    # 解析JSON格式，提取实际的用户说话内容
    display_text = text
    try:
        # 尝试解析JSON格式
        if text.strip().startswith("{") and text.strip().endswith("}"):
            parsed_data = json.loads(text)
            if isinstance(parsed_data, dict) and "content" in parsed_data:
                # 如果是包含说话人信息的JSON格式，只显示content部分
                display_text = parsed_data["content"]
                # 保存说话人信息到conn对象
                if "speaker" in parsed_data:
                    conn.current_speaker = parsed_data["speaker"]
    except (json.JSONDecodeError, TypeError):
        # 如果不是JSON格式，直接使用原始文本
        display_text = text
    stt_text = textUtils.get_string_no_punctuation_or_emoji(display_text)
    await conn.websocket.send(
        json.dumps({"type": "stt", "text": stt_text, "session_id": conn.session_id})
    )


async def send_display_message(conn: "ConnectionHandler", text):
    """发送纯显示消息"""
    message = {
        "type": "stt",
        "text": text,
        "session_id": conn.session_id
    }
    await conn.websocket.send(json.dumps(message))
