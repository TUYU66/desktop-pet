"""Chat report queue and persistence adapter; session owns its queue lifecycle."""

import time
import os
import json
import subprocess as _sp
from typing import Any, Protocol
from queue import Queue
from config.manage_api_client import report as manage_report
from core.utils.textUtils import normalize_spoken_text


class ReportContext(Protocol):
    """Minimal reporting port; no dependency on a WebSocket connection class."""

    device_id: str | None
    session_id: str
    logger: Any
    report_enabled: bool
    report_asr_enable: bool
    report_tts_enable: bool
    chat_history_conf: int
    report_queue: Queue


TAG = __name__


async def report(conn: ReportContext, type, text, opus_data, report_time):
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


def opus_to_wav(conn: ReportContext, opus_data):
    """将Opus数据转换为WAV格式的字节流"""
    try:
        all_opus = b"".join(opus_data)
        proc = _sp.run(
            ["ffmpeg", "-y", "-f", "opus", "-ar", "16000", "-ac", "1", "-i", "-", "-f", "wav", "-"],
            input=all_opus,
            capture_output=True,
            timeout=10,
        )
        if proc.stdout:
            return proc.stdout
    except Exception as e:
        conn.logger.bind(tag=TAG).debug(f"opus_to_wav 转换失败: {e}")
    return None


def enqueue_tts_report(conn: ReportContext, text, opus_data):
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
            conn.logger.bind(tag=TAG).debug(f"TTS数据已加入上报队列: {conn.device_id}, 不上报音频")
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"加入TTS上报队列失败: {text}, {e}")


def enqueue_tool_report(
    conn: ReportContext,
    tool_name: str,
    tool_input: dict,
    tool_result: str = None,
    report_tool_call: bool = True,
):
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
            result_content = json.dumps(
                [{"type": "tool_result", "text": result_display}], ensure_ascii=False
            )
            conn.report_queue.put((3, result_content, None, timestamp + 1))
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"加入工具上报队列失败: {e}")


def enqueue_asr_report(conn: ReportContext, text, opus_data):
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
            conn.logger.bind(tag=TAG).debug(f"ASR数据已加入上报队列: {conn.device_id}, 不上报音频")
    except Exception as e:
        conn.logger.bind(tag=TAG).debug(f"加入ASR上报队列失败: {text}, {e}")


def get_report_session_id(conn):
    """获取 Java 端的角色 session ID，确保前后端看到同一条聊天记录"""
    try:
        role_id = conn.config.get("activeRole", "default")
        role_name = ""
        roles_raw = conn.config.get("roles", "[]")
        try:
            roles = (
                json.loads(roles_raw)
                if isinstance(roles_raw, str)
                else roles_raw if isinstance(roles_raw, list) else []
            )
            for r in roles:
                if r.get("id") == role_id:
                    role_name = r.get("name", role_name)
                    break
        except Exception:
            pass
        import httpx

        resp = httpx.get(
            f"http://localhost:8000/xiaozhi/api/chat/session-by-role/{role_id}",
            params={"roleName": role_name},
            headers={"Service-Key": os.environ.get("XIAOZHI_PYTHON_SERVICE_KEY", "")},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            if data and data.get("sessionId"):
                return data["sessionId"]
    except Exception as e:
        conn.logger.bind(tag=TAG).debug(f"获取Java session失败，使用本地ID: {e}")
    return conn.session_id


def process_report(conn, type, text, audio_data, report_time):
    """处理上报任务（同步HTTP POST，不依赖事件循环）"""
    try:
        import httpx

        session_id = get_report_session_id(conn)
        url = "http://localhost:8000/xiaozhi/api/chat/report"
        payload = {
            "sessionId": session_id,
            "chatType": str(type),
            "content": text,
            "macAddress": conn.device_id or "unknown",
            "reportTime": report_time,
        }
        resp = httpx.post(
            url,
            json=payload,
            headers={"Service-Key": os.environ.get("XIAOZHI_PYTHON_SERVICE_KEY", "")},
            timeout=10,
        )
        if resp.status_code != 200:
            conn.logger.bind(tag=TAG).error(f"上报失败: HTTP {resp.status_code}")
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"上报处理异常: {e}")
