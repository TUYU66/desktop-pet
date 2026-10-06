"""
Opus编码工具类
将PCM音频数据编码为Opus格式

两套方案：
  1. opuslib_next.Encoder（流式，微秒级 < 1μs/帧）
     用于对话过程中 TTS 实时编码发送
  2. ffmpeg + OGG 容器（非流式，全覆盖编码）
     用于预录制音频文件 → Opus 帧列表（唤醒词、提示音等）
"""

import subprocess
import tempfile
import os
from typing import Optional, Callable, Any

import opuslib_next


class OpusEncoderUtils:
    """PCM到Opus的编码器 — 基于 opuslib_next.Encoder（流式）"""

    def __init__(self, sample_rate: int, channels: int, frame_size_ms: int):
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size_ms = frame_size_ms
        self.frame_size = (sample_rate * frame_size_ms) // 1000  # samples / frame
        self.frame_bytes = self.frame_size * 2  # 16bit = 2 bytes / sample
        self.buffer = b""

        # 创建 opuslib_next 编码器（默认 bitrate，保持语音质量）
        self._encoder = opuslib_next.Encoder(
            sample_rate, channels, opuslib_next.APPLICATION_AUDIO
        )
        # 限制最高比特率，避免解码端 PSRAM 瞬时负载过高
        self._encoder.bitrate = 20000

    def reset_state(self):
        """重置内部缓冲区（会话切换时调用）"""
        self.buffer = b""

    def encode_pcm_to_opus_stream(
        self, pcm_data: bytes, end_of_stream: bool, callback: Callable[[Any], Any]
    ):
        """逐帧编码 PCM → Opus，每编完一帧立即回调

        Args:
            pcm_data: PCM 原始数据（16bit 小端，单声道）
            end_of_stream: 是否结束（缓冲区的剩余数据也补零编码输出）
            callback: 编码完成一帧后的回调 fn(opu| 裸帧 bytes)
        """
        self.buffer += pcm_data

        # 按帧大小切分编码
        while len(self.buffer) >= self.frame_bytes:
            frame = self.buffer[: self.frame_bytes]
            self.buffer = self.buffer[self.frame_bytes :]
            opus_data = self._encoder.encode(frame, self.frame_size)
            if opus_data:
                callback(opus_data)

        # 流结束时处理剩余的不足一帧的数据
        if end_of_stream and self.buffer:
            # 补零至完整帧
            frame = self.buffer.ljust(self.frame_bytes, b"\x00")
            opus_data = self._encoder.encode(frame, self.frame_size)
            if opus_data:
                callback(opus_data)
            self.buffer = b""

    def close(self):
        """释放编码器资源"""
        self._encoder = None
        self.buffer = b""


# ── 以下是 ffmpeg + OGG 方案（非流式，用于文件场景）──

def encode_pcm_to_opus_ogg(pcm_data: bytes, sample_rate: int = 24000,
                            channels: int = 1, frame_duration: int = 60) -> list[bytes]:
    """用 ffmpeg 将 PCM 批量编码为裸 Opus 帧列表（通过 OGG 容器中转）

    适用于预录制音频（唤醒词、提示音）的一次性编码，不用于流式对话。

    Args:
        pcm_data: 完整 PCM 数据
        sample_rate: 采样率
        channels: 声道数
        frame_duration: 帧时长（ms）

    Returns:
        裸 Opus 帧列表
    """
    datas = []
    import struct

    tmp = tempfile.NamedTemporaryFile(suffix=".opus", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels),
                "-i", "-",
                "-c:a", "libopus", "-b:a", "24k",
                "-frame_duration", str(frame_duration),
                "-vbr", "off",
                tmp_path,
            ],
            input=pcm_data,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg exit={proc.returncode}")

        with open(tmp_path, "rb") as f:
            ogg_data = f.read()
        if not ogg_data:
            return datas

        # 从 OGG 提取裸 Opus 帧（跳过 OpusHead 和 OpusTags）
        pos = 0
        pkt_idx = 0
        while pos < len(ogg_data):
            if ogg_data[pos : pos + 4] != b"OggS":
                break
            nseg = ogg_data[pos + 26]
            segs = list(ogg_data[pos + 27 : pos + 27 + nseg])
            dstart = pos + 27 + nseg
            spos = dstart

            frame_accum = bytearray()
            for sz in segs:
                if sz > 0:
                    frame_accum.extend(ogg_data[spos : spos + sz])
                    spos += sz
                    if sz < 255:
                        if pkt_idx >= 2:  # 跳过 OpusHead + OpusTags
                            datas.append(bytes(frame_accum))
                        pkt_idx += 1
                        frame_accum = bytearray()
                else:
                    if pkt_idx >= 2:
                        datas.append(bytes(frame_accum))
                    pkt_idx += 1
                    frame_accum = bytearray()

            if frame_accum:
                if pkt_idx >= 2:
                    datas.append(bytes(frame_accum))
                pkt_idx += 1

            pos = spos  # 使用 spos（经过所有 segment 后的位置）而不是 sum(segs)

    except Exception as e:
        from config.logger import setup_logging
        logger = setup_logging()
        logger.bind(tag="OpusUtils").error(f"批量编码异常: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return datas
