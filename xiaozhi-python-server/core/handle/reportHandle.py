"""
TTS上报功能已集成到ConnectionHandler类中。

上报功能包括：
1. 每个连接对象拥有自己的上报队列和处理线程
2. 上报线程的生命周期与连接对象绑定
3. 使用本模块的enqueue_tts_report入口进行上报

具体实现请参考core/transport/connection.py中的相关代码。
"""

import time
import json
import subprocess as _sp
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.transport.connection import ConnectionHandler

from config.manage_api_client import report as manage_report
from core.utils.textUtils import normalize_spoken_text

TAG = __name__


async def report(conn: "ConnectionHandler", type, text, opus_data, report_time):
    """执行聊天记录上报操作"""
    try:
        await manage_report(
            mac_address=conn.device_id or "unknown",
            session_id=conn.session_id,
            chat_type=type,
            content=text,
            audio=opus_data,
            report_time=report_time,
            device_id=conn.device_id,
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"聊天记录上报失败: {e}")


def opus_to_wav(conn: "ConnectionHandler", opus_data):
    """将Opus数据转换为WAV格式的字节流"""
    try:
        all_opus = b"".join(opus_data)
        proc = _sp.run(
            ["ffmpeg", "-y", "-f", "opus", "-ar", "16000", "-ac", "1",
             "-i", "-", "-f", "wav", "-"],
            input=all_opus, capture_output=True, timeout=10
        )
        if proc.stdout:
            return proc.stdout
    except Exception as e:
        conn.logger.bind(tag=TAG).debug(f"opus_to_wav 转换失败: {e}")
    return None


def enqueue_tts_report(conn: "ConnectionHandler", text, opus_data):
    text = normalize_spoken_text(text)
    if not conn.report_enabled or not conn.report_tts_enable:
        return
    if conn.chat_history_conf == 0:
        return
    """将TTS数据加入上报队列

    Args:
        conn: 连接对象
        text: 合成文本
        opus_data: opus音频数据
    """
    try:
        # 使用连接对象的队列，传入文本和二进制数据而非文件路径
        if conn.chat_history_conf == 2:
            conn.report_queue.put((2, text, opus_data, int(time.time() * 1000)))
            conn.logger.bind(tag=TAG).debug(
                f"TTS数据已加入上报队列: {conn.device_id}, 音频大小: {len(opus_data)} "
            )
        else:
            conn.report_queue.put((2, text, None, int(time.time() * 1000)))
            conn.logger.bind(tag=TAG).debug(
                f"TTS数据已加入上报队列: {conn.device_id}, 不上报音频"
            )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"加入TTS上报队列失败: {text}, {e}")


def enqueue_tool_report(conn: "ConnectionHandler", tool_name: str, tool_input: dict, tool_result: str = None, report_tool_call: bool = True):
    if not conn.report_enabled:
        return
    if conn.chat_history_conf == 0:
        return

    try:
        timestamp = int(time.time() * 1000)

        # 构建工具调用内容
        if report_tool_call:
            tool_text = json.dumps(
                [
                    {
                        "type": "tool",
                        "text": f"{tool_name}({json.dumps(tool_input, ensure_ascii=False)})",
                    }
                ]
            )
            conn.report_queue.put((3, tool_text, None, timestamp))

        # 构建工具结果内容
        if tool_result:
            result_display = f'{{"result":"{str(tool_result)}"}}'
            result_content = json.dumps([{"type": "tool_result", "text": result_display}], ensure_ascii=False)
            conn.report_queue.put((3, result_content, None, timestamp + 1))
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"加入工具上报队列失败: {e}")


def enqueue_asr_report(conn: "ConnectionHandler", text, opus_data):
    if not conn.report_enabled or not conn.report_asr_enable:
        return
    if conn.chat_history_conf == 0:
        return
    """将ASR数据加入上报队列

    Args:
        conn: 连接对象
        text: 合成文本
        opus_data: opus音频数据
    """
    try:
        # 使用连接对象的队列，传入文本和二进制数据而非文件路径
        if conn.chat_history_conf == 2:
            conn.report_queue.put((1, text, opus_data, int(time.time() * 1000)))
            conn.logger.bind(tag=TAG).debug(
                f"ASR数据已加入上报队列: {conn.device_id}, 音频大小: {len(opus_data)} "
            )
        else:
            conn.report_queue.put((1, text, None, int(time.time() * 1000)))
            conn.logger.bind(tag=TAG).debug(
                f"ASR数据已加入上报队列: {conn.device_id}, 不上报音频"
            )
    except Exception as e:
        conn.logger.bind(tag=TAG).debug(f"加入ASR上报队列失败: {text}, {e}")
