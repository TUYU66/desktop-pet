"""Late recognition must not revive a canceled interaction."""
import asyncio
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from core.providers.asr.base import ASRProviderBase


class Provider(ASRProviderBase):
    async def speech_to_text(self, *args, **kwargs):
        return '', None


class AsrTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_result_after_standby_is_not_displayed_or_routed(self):
        provider = Provider()
        conn = NS(input_generation=1, session_id='session', audio_format='opus',
                  stop_event=threading.Event(), standby=False)
        started, release = asyncio.Event(), asyncio.Event()
        async def recognize(*args):
            started.set()
            await release.wait()
            return '这是旧识别结果', None
        provider.speech_to_text_wrapper = recognize
        with patch('core.providers.asr.base.startToChat', new_callable=AsyncMock) as route:
            task = asyncio.create_task(provider.handle_voice_stop(conn, [b'audio']))
            await started.wait()
            conn.input_generation += 1
            conn.standby = True
            release.set()
            await task
            route.assert_not_awaited()

    async def test_pending_old_turn_is_skipped_and_new_turn_is_recognized(self):
        provider = Provider()
        conn = NS(input_generation=2, session_id='new', stop_event=threading.Event(),
                  _asr_utterances=asyncio.Queue(maxsize=2))
        conn._asr_utterances.put_nowait(([b'old'], 1, 'old'))
        conn._asr_utterances.put_nowait(([b'new'], 2, 'new'))
        provider.handle_voice_stop = AsyncMock()
        await provider._recognize_utterances(conn)
        provider.handle_voice_stop.assert_awaited_once_with(conn, [b'new'])
