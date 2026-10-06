import os
import io
import re
import sys
import time
import shutil
import psutil
import asyncio
import threading

from funasr import AutoModel
from config.logger import setup_logging
from typing import Optional, Tuple, List
from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType

TAG = __name__
logger = setup_logging()

MAX_RETRIES = 2
RETRY_DELAY = 1  # 重试延迟（秒）


# FunASR 情绪 → emoji 映射
EMOTION_EMOJI_MAP = {
    "SAD": "😔",
    "HAPPY": "🙂",
    "ANGRY": "😡",
    "NEUTRAL": "😐",
    "FEARFUL": "😨",
    "DISGUSTED": "🤢",
    "SURPRISED": "😮",
}


def lang_tag_filter(text: str) -> dict | str:
    """解析 FunASR 标签格式识别结果，返回 dict 或纯文本"""
    tag_pattern = r"<\|([^|]+)\|>"
    all_tags = re.findall(tag_pattern, text)
    clean_text = re.sub(tag_pattern, "", text).strip()

    if not all_tags:
        return clean_text

    result = {"content": clean_text}
    if all_tags:
        result["language"] = all_tags[0]
    if len(all_tags) > 1:
        emotion = all_tags[1]
        result["emotion"] = emotion
        result["emoji"] = EMOTION_EMOJI_MAP.get(emotion, "")
    return result


# 捕获标准输出
class CaptureOutput:
    def __enter__(self):
        self._output = io.StringIO()
        self._original_stdout = sys.stdout
        sys.stdout = self._output

    def __exit__(self, exc_type, exc_value, traceback):
        sys.stdout = self._original_stdout
        self.output = self._output.getvalue()
        self._output.close()

        # 将捕获到的内容通过 logger 输出
        if self.output:
            logger.bind(tag=TAG).info(self.output.strip())


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__()
        
        # 内存检测，要求大于2G
        min_mem_bytes = 2 * 1024 * 1024 * 1024
        total_mem = psutil.virtual_memory().total
        if total_mem < min_mem_bytes:
            logger.bind(tag=TAG).error(f"可用内存不足2G，当前仅有 {total_mem / (1024*1024):.2f} MB，可能无法启动FunASR")
        
        self.interface_type = InterfaceType.LOCAL
        self._inference_lock = threading.Lock()
        self.language = config.get("language", "zh")
        self.model_dir = config.get("model_dir")
        self.output_dir = config.get("output_dir")  # 修正配置键名
        self.delete_audio_file = delete_audio_file

        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)
        with CaptureOutput():
            self.model = AutoModel(
                model=self.model_dir,
                vad_kwargs={"max_single_segment_time": 30000},
                disable_update=True,
                hub="hf",
                # device="cuda:0",  # 启用GPU加速
            )

    async def speech_to_text(
        self, opus_data: List[bytes], session_id: str, audio_format="opus", artifacts=None
    ) -> Tuple[Optional[str], Optional[str]]:
        """语音转文本主处理逻辑"""
        retry_count = 0
        
        while retry_count < MAX_RETRIES:
            try:
                if artifacts is None:
                    return "", None

                # 语音识别 - 使用线程池避免阻塞事件循环
                start_time = time.time()
                def infer():
                    # Cancelled asyncio work cannot terminate model.generate.
                    # Do not pile up additional inference threads behind it.
                    if not self._inference_lock.acquire(blocking=False):
                        raise RuntimeError("上一段语音识别尚未结束")
                    try:
                        return self.model.generate(input=artifacts.pcm_bytes, cache={},
                            language=self.language, use_itn=True, batch_size_s=60)
                    finally:
                        self._inference_lock.release()
                result = await asyncio.to_thread(infer)
                if not result:
                    return "", artifacts.file_path
                # Some model pipelines return multiple segments. Keep every
                # segment instead of answering from the first half alone.
                segments = [lang_tag_filter(item.get("text", ""))
                            for item in result if isinstance(item, dict)]
                if not segments:
                    return "", artifacts.file_path
                content = "".join(segment.get("content", "") if isinstance(segment, dict)
                                  else segment for segment in segments)
                if isinstance(segments[0], dict):
                    text = dict(segments[0], content=content)
                else:
                    text = content
                content = text.get('content', '') if isinstance(text, dict) else text
                logger.bind(tag=TAG).debug(
                    f"语音识别耗时: {time.time() - start_time:.3f}s | 结果: {content}"
                )

                return text, artifacts.file_path

            except OSError as e:
                retry_count += 1
                if retry_count >= MAX_RETRIES:
                    logger.bind(tag=TAG).error(
                        f"语音识别失败（已重试{retry_count}次）: {e}", exc_info=True
                    )
                    return "", None
                logger.bind(tag=TAG).warning(
                    f"语音识别失败，正在重试（{retry_count}/{MAX_RETRIES}）: {e}"
                )
                await asyncio.sleep(RETRY_DELAY)

            except Exception as e:
                logger.bind(tag=TAG).error(f"语音识别失败: {e}", exc_info=True)
                return "", None
