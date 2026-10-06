import os
import io
import wave
import uuid
import json
import time
import queue
import shutil
import asyncio
import tempfile
import traceback
import threading
import opuslib_next

from abc import ABC, abstractmethod
from config.logger import setup_logging
from core.providers.asr.dto.dto import InterfaceType
from core.handle.receiveAudioHandle import startToChat
from core.handle.reportHandle import enqueue_asr_report
from core.utils.util import remove_punctuation_and_length
from core.handle.receiveAudioHandle import handleAudioMessage
from typing import Optional, Tuple, List, NamedTuple, TYPE_CHECKING


if TYPE_CHECKING:
    from core.transport.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class ASRProviderBase(ABC):
    def __init__(self):
        pass

    # 打开音频通道
    async def open_audio_channels(self, conn: "ConnectionHandler"):
        conn.asr_priority_thread = threading.Thread(
            target=self.asr_text_priority_thread, args=(conn,), daemon=True
        )
        conn.asr_priority_thread.start()

    # 有序处理ASR音频
    def asr_text_priority_thread(self, conn: "ConnectionHandler"):
        while not conn.stop_event.is_set():
            try:
                message = conn.asr_audio_queue.get(timeout=1)
                future = asyncio.run_coroutine_threadsafe(
                    handleAudioMessage(conn, message),
                    conn.loop,
                )
                future.result(timeout=120)
            except queue.Empty:
                continue
            except TimeoutError:
                future.cancel()
                conn.input_generation = getattr(conn, 'input_generation', 0) + 1
                logger.bind(tag=TAG).warning("ASR 处理超时（>120s），跳过当前音频帧")
                continue
            except Exception as e:
                logger.bind(tag=TAG).error(
                    f"处理ASR文本失败: {str(e)}, 类型: {type(e).__name__}, 堆栈: {traceback.format_exc()}"
                )
                continue

    # 接收音频
    async def receive_audio(self, conn: "ConnectionHandler", audio, audio_have_voice):
        if conn.client_listen_mode == "manual":
            # 手动模式：缓存音频用于ASR识别
            conn.asr_audio.append(audio)
        else:
            # 自动/实时模式：使用VAD检测
            conn.asr_audio.append(audio)

            # 如果没有语音，且之前也没有声音，缓存部分音频
            if not audio_have_voice and not conn.client_have_voice:
                conn.asr_audio = conn.asr_audio[-10:]
                return

            # 自动模式下通过VAD检测到语音停止时触发识别
            if conn.asr.interface_type != InterfaceType.STREAM and conn.client_voice_stop:
                asr_audio_task = conn.asr_audio.copy()
                conn.reset_audio_states()

                # VAD already confirmed speech. Packet count depends on frame
                # duration and must not silently reject short commands.
                if asr_audio_task:
                    self.queue_utterance(conn, asr_audio_task)

    def queue_utterance(self, conn, audio):
        """Bound complete utterances separately from microphone/VAD processing."""
        if not hasattr(conn, '_asr_utterances'):
            conn._asr_utterances = asyncio.Queue(maxsize=2)
        queue_ = conn._asr_utterances
        if queue_.full():
            queue_.get_nowait()
            logger.bind(tag=TAG).warning('语音识别积压，丢弃较早的待识别片段')
        queue_.put_nowait((audio, getattr(conn, 'input_generation', 0), conn.session_id))
        if not getattr(conn, '_asr_worker', None) or conn._asr_worker.done():
            conn._asr_worker = asyncio.create_task(self._recognize_utterances(conn))

    async def _recognize_utterances(self, conn):
        while not conn.stop_event.is_set() and not conn._asr_utterances.empty():
            audio, generation, session = conn._asr_utterances.get_nowait()
            if generation != getattr(conn, 'input_generation', 0) or session != conn.session_id:
                continue
            try:
                await asyncio.wait_for(self.handle_voice_stop(conn, audio), timeout=30)
            except asyncio.TimeoutError:
                logger.bind(tag=TAG).warning('语音识别超过30秒，已取消本段')
                if generation == getattr(conn, 'input_generation', 0) and not conn.stop_event.is_set():
                    try:
                        await self._send_recognition_state(conn, "timeout")
                        await conn.websocket.send(json.dumps({'type': 'status', 'text': '这次识别超时，请再说一次。'}))
                    except Exception:
                        return
            except Exception as error:
                logger.bind(tag=TAG).warning('语音片段处理失败: {}', type(error).__name__)

    async def _send_recognition_state(self, conn, state):
        if (getattr(conn, "features", {}) or {}).get("asr_status"):
            await conn.websocket.send(json.dumps({
                "type": "asr", "state": state, "session_id": conn.session_id,
            }))

    # 处理语音停止
    async def handle_voice_stop(self, conn: "ConnectionHandler", asr_audio_task: List[bytes]):
        """执行ASR识别"""
        generation = getattr(conn, 'input_generation', 0)
        session = conn.session_id
        try:
            total_start_time = time.monotonic()
            await self._send_recognition_state(conn, "start")

            asr_task = self.speech_to_text_wrapper(
                asr_audio_task, conn.session_id, conn.audio_format
            )
            asr_result = await asr_task
            if (generation != getattr(conn, 'input_generation', 0) or session != conn.session_id
                    or conn.stop_event.is_set() or getattr(conn, 'standby', False)):
                return

            if isinstance(asr_result, Exception):
                logger.bind(tag=TAG).error(f"ASR识别失败: {asr_result}")
                raw_text = ""
            else:
                raw_text, _ = asr_result

            if isinstance(raw_text, dict):
                if raw_text.get("language"):
                    logger.bind(tag=TAG).info(f"识别语言: {raw_text['language']}")
                if raw_text.get("emotion"):
                    logger.bind(tag=TAG).info(f"识别情绪: {raw_text['emotion']}")
                if raw_text.get("content"):
                    logger.bind(tag=TAG).info(f"识别文本: {raw_text['content']}")
                enhanced_text = raw_text.get("content", json.dumps(raw_text, ensure_ascii=False))
                content_for_length_check = raw_text.get("content", "")
            else:
                if raw_text:
                    logger.bind(tag=TAG).info(f"识别文本: {raw_text}")
                enhanced_text = raw_text
                content_for_length_check = raw_text

            # 性能监控
            total_time = time.monotonic() - total_start_time
            logger.bind(tag=TAG).debug(f"总处理耗时: {total_time:.3f}s")

            # 检查文本长度
            text_len, _ = remove_punctuation_and_length(content_for_length_check)
            self.stop_ws_connection()

            if text_len > 0:
                audio_snapshot = asr_audio_task.copy()
                enqueue_asr_report(conn, enhanced_text, audio_snapshot)
                # 使用自定义模块进行上报
                await startToChat(conn, enhanced_text)
            else:
                await self._send_recognition_state(conn, "empty")
        except Exception as e:
            logger.bind(tag=TAG).error(f"处理语音停止失败: {e}")
            import traceback

            logger.bind(tag=TAG).debug(f"异常详情: {traceback.format_exc()}")
            # A failed ASR attempt is finished too. Retire its LCD processing
            # hint without overwriting a newer utterance or waking a standby device.
            if (generation == getattr(conn, 'input_generation', 0) and session == conn.session_id
                    and not conn.stop_event.is_set() and not getattr(conn, 'standby', False)):
                try:
                    await self._send_recognition_state(conn, "error")
                except Exception:
                    pass  # The original failure is already logged; transport may be down.

    def _build_enhanced_text(self, text: str, speaker_name: Optional[str]) -> str:
        """构建包含说话人信息的文本（仅用于纯文本ASR）"""
        if speaker_name and speaker_name.strip():
            return json.dumps(
                {"speaker": speaker_name, "content": text}, ensure_ascii=False
            )
        else:
            return text

    def _pcm_to_wav(self, pcm_data: bytes) -> bytes:
        """将PCM数据转换为WAV格式"""
        if len(pcm_data) == 0:
            logger.bind(tag=TAG).warning("PCM数据为空，无法转换WAV")
            return b""

        # 确保数据长度是偶数（16位音频）
        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]

        # 创建WAV文件头
        wav_buffer = io.BytesIO()
        try:
            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file.setnchannels(1)  # 单声道
                wav_file.setsampwidth(2)  # 16位
                wav_file.setframerate(16000)  # 16kHz采样率
                wav_file.writeframes(pcm_data)

            wav_buffer.seek(0)
            wav_data = wav_buffer.read()

            return wav_data
        except Exception as e:
            logger.bind(tag=TAG).error(f"WAV转换失败: {e}")
            return b""

    def stop_ws_connection(self):
        pass

    async def close(self):
        pass

    class AudioArtifacts(NamedTuple):
        pcm_frames: List[bytes]
        """PCM音频帧列表"""
        pcm_bytes: bytes
        """合并后的PCM音频字节数据"""
        file_path: Optional[str]
        """WAV文件路径"""
        temp_path: Optional[str]
        """临时WAV文件路径"""

    def get_current_artifacts(self) -> Optional["ASRProviderBase.AudioArtifacts"]:
        return self._current_artifacts

    def requires_file(self) -> bool:
        """是否需要文件输入"""
        return False

    def prefers_temp_file(self) -> bool:
        """是否优先使用临时文件"""
        return False

    def build_temp_file(self, pcm_bytes: bytes) -> Optional[str]:
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name
            with wave.open(temp_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(pcm_bytes)
            return temp_path
        except Exception as e:
            logger.bind(tag=TAG).error(f"临时音频文件生成失败: {e}")
            return None

    def save_audio_to_file(self, pcm_data: List[bytes], session_id: str) -> str:
        """PCM数据保存为WAV文件"""
        module_name = __name__.split(".")[-1]
        file_name = f"asr_{module_name}_{session_id}_{uuid.uuid4()}.wav"
        file_path = os.path.join(self.output_dir, file_name)

        with wave.open(file_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 2 bytes = 16-bit
            wf.setframerate(16000)
            wf.writeframes(b"".join(pcm_data))

        return file_path

    async def speech_to_text_wrapper(
        self, opus_data: List[bytes], session_id: str, audio_format="opus"
    ) -> Tuple[Optional[str], Optional[str]]:
        file_path = None
        temp_path = None
        try:
            if audio_format == "pcm":
                pcm_data = opus_data
            else:
                pcm_data = self.decode_opus(opus_data)
            combined_pcm_data = b"".join(pcm_data)

            free_space = shutil.disk_usage(self.output_dir).free
            if free_space < len(combined_pcm_data) * 2:
                raise OSError("磁盘空间不足")

            if self.requires_file() and self.prefers_temp_file():
                temp_path = self.build_temp_file(combined_pcm_data)

            if (hasattr(self, "delete_audio_file") and not self.delete_audio_file) or (
                self.requires_file() and not self.prefers_temp_file()
            ):
                file_path = self.save_audio_to_file(pcm_data, session_id)

            if len(combined_pcm_data) == 0:
                artifacts = None
            else:
                artifacts = ASRProviderBase.AudioArtifacts(
                    pcm_frames=pcm_data,
                    pcm_bytes=combined_pcm_data,
                    file_path=file_path,
                    temp_path=temp_path,
                )

            text, _ = await self.speech_to_text(
                opus_data, session_id, audio_format, artifacts
            )
            return text, file_path
        except OSError as e:
            logger.bind(tag=TAG).error(f"文件操作错误: {e}")
            return None, None
        except Exception as e:
            logger.bind(tag=TAG).error(f"语音识别失败: {e}")
            return None, None
        finally:
            try:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
                if (
                    hasattr(self, "delete_audio_file")
                    and self.delete_audio_file
                    and file_path
                    and os.path.exists(file_path)
                ):
                    os.remove(file_path)
            except Exception as e:
                logger.bind(tag=TAG).error(f"文件清理失败: {e}")

    @abstractmethod
    async def speech_to_text(
        self,
        opus_data: List[bytes],
        session_id: str,
        audio_format="opus",
        artifacts: Optional[AudioArtifacts] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """将语音数据转换为文本

        :param opus_data: 输入的Opus音频数据
        :param session_id: 会话ID
        :param audio_format: 音频格式，默认"opus"
        :param artifacts: 音频工件，包含PCM数据、文件路径等
        :return: 识别结果文本和文件路径（如果有）
        """
        pass

    @staticmethod
    def decode_opus(opus_data: List[bytes]) -> List[bytes]:
        """将Opus音频数据解码为PCM数据（使用opuslib_next，与VAD一致）

        ESP32 发送的是裸 Opus 帧（不带 OGG 容器），ffmpeg -f opus 无法解码。
        VAD 已经用 opuslib_next 成功解码，此处复用同一方式。
        """
        try:
            import opuslib_next
            decoder = opuslib_next.Decoder(16000, 1)
            pcm_frames = []
            for packet in opus_data:
                try:
                    pcm_frame = decoder.decode(packet, 960)
                    pcm_frames.append(pcm_frame)
                except opuslib_next.OpusError as e:
                    logger.bind(tag=TAG).warning(f"Opus帧解码失败（跳过）: {e}")
                    continue
            return pcm_frames
        except Exception as e:
            logger.bind(tag=TAG).error(f"Opus解码失败: {e}")
        return []
