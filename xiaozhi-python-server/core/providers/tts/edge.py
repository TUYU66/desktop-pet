import os
import uuid
import edge_tts
import asyncio
import time
from datetime import datetime
from core.providers.tts.base import TTSProviderBase
from core.providers.tts.dto.dto import SentenceType
from core.utils.tts import MarkdownCleaner
from core.utils.opus_encoder_utils import OpusEncoderUtils
from config.logger import setup_logging


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice")
        self.audio_file_type = config.get("format", "mp3")

    def generate_filename(self, extension=".mp3"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    def to_tts_stream(self, text, opus_handler=None):
        # Keep the file/cache API unchanged; only live replies use incremental decoding.
        if not self.conn.config.get('tts_incremental_decode', True) or self.conn.audio_format != 'opus':
            return super().to_tts_stream(text, opus_handler)
        sentence = getattr(self, 'current_sentence_id', None)
        cancelled = lambda: self.conn.client_abort or sentence != self.conn.sentence_id or self.conn.stop_event.is_set()
        if cancelled(): return
        spoken = MarkdownCleaner.clean_markdown(text)
        if self._correct_words_pattern:
            spoken = self._correct_words_pattern.sub(lambda m: self.correct_words[m.group(0)], spoken)
        emitted = False

        async def stream():
            nonlocal emitted
            started = time.monotonic()
            rate = self.conn.sample_rate
            encoder = OpusEncoderUtils(rate, 1, 60)
            options = {'creationflags': 0x08000000} if os.name == 'nt' else {}
            process = await asyncio.create_subprocess_exec('ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-f', 'mp3', '-i', 'pipe:0', '-f', 's16le', '-ac', '1', '-ar', str(rate), 'pipe:1',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, **options)

            async def feed():
                communicate = edge_tts.Communicate(spoken, voice=self.voice or 'zh-CN-XiaoxiaoNeural')
                try:
                    async for chunk in communicate.stream():
                        if cancelled(): raise asyncio.CancelledError()
                        if chunk['type'] == 'audio':
                            process.stdin.write(chunk['data'])
                            await process.stdin.drain()
                finally:
                    process.stdin.close()

            def packet(data):
                nonlocal emitted
                if cancelled(): return
                if not emitted:
                    self.tts_audio_queue.put((SentenceType.FIRST, None, text, sentence))
                    setup_logging().bind(tag='EdgeTTS').info('voice_timing first_audio_ms={}', round((time.monotonic()-started)*1000))
                    emitted = True
                if opus_handler: opus_handler(data)

            async def decode():
                while True:
                    if cancelled(): raise asyncio.CancelledError()
                    while (self.tts_audio_queue.qsize() + len(getattr(getattr(self.conn, 'audio_rate_controller', None), 'queue', ()))) > 32:
                        if cancelled(): raise asyncio.CancelledError()
                        await asyncio.sleep(.02)
                    try:
                        pcm = await process.stdout.readexactly(encoder.frame_bytes)
                        final = False
                    except asyncio.IncompleteReadError as error:
                        pcm, final = error.partial, True
                    if pcm: encoder.encode_pcm_to_opus_stream(pcm, end_of_stream=final, callback=packet)
                    if final: break

            tasks = [asyncio.create_task(feed()), asyncio.create_task(decode())]
            async def monitor():
                while not all(task.done() for task in tasks):
                    if cancelled():
                        for task in tasks: task.cancel()
                        return
                    await asyncio.sleep(.04)
            watcher = asyncio.create_task(monitor())
            try:
                await asyncio.gather(*tasks)
                code = await process.wait()
                if code or not emitted: raise RuntimeError('流式语音转码没有完成')
                setup_logging().bind(tag='EdgeTTS').info('voice_timing segment_ms={}', round((time.monotonic()-started)*1000))
                return True
            finally:
                watcher.cancel()
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(watcher, *tasks, return_exceptions=True)
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                encoder.close()

        try:
            ok = self._run_async_with_timeout(stream, timeout=max(30, self.tts_timeout, min(180, len(spoken) * .5 + 15)))
        except asyncio.CancelledError:
            return
        if cancelled(): return
        if not ok:
            if not emitted:
                # Never replay a partially heard segment on fallback.
                return super().to_tts_stream(text, opus_handler)
            from core.conversation.requests import response_error
            response_error(self.conn, sentence)

    async def text_to_speak(self, text, output_file):
        try:
            # 确保 voice 有值，日志记录实际使用的音色
            voice = self.voice or "zh-CN-XiaoxiaoNeural"
            if voice != self.voice:
                self.voice = voice
            from config.logger import setup_logging
            setup_logging().bind(tag="EdgeTTS").info(f"TTS voice={voice}, text_len={len(text)}")
            communicate = edge_tts.Communicate(text, voice=voice)
            if output_file:
                # 确保目录存在并创建空文件
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, "wb") as f:
                    pass

                # 流式写入音频数据
                with open(output_file, "ab") as f:  # 改为追加模式避免覆盖
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":  # 只处理音频数据块
                            f.write(chunk["data"])
            else:
                # 返回音频二进制数据
                audio_bytes = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes += chunk["data"]
                return audio_bytes
        except Exception as e:
            error_msg = f"Edge TTS请求失败: {e}"
            raise Exception(error_msg)  # 抛出异常，让调用方捕获
