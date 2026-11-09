import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from core.handle import helloHandle as hello


class WakeupVoiceTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return NS(config={'enable_wakeup_words_response_cache': True, 'wakeup_words': ['小兰小兰']},
                  tts=NS(voice='selected-voice', to_tts=Mock(return_value=[b'current-voice'])),
                  stop_event=threading.Event(), input_generation=0, client_abort=False,
                  sentence_id='old', dialogue=Mock(), logger=Mock())

    async def test_cold_or_wrong_voice_cache_uses_configured_tts_not_default_recording(self):
        for cached in (None, {'voice': 'other-voice', 'file_path': 'other.wav', 'text': 'old'}):
            conn = self.context()
            with patch.object(hello.wakeup_words_config, 'get_wakeup_response', return_value=cached), \
                    patch.object(hello, 'audio_to_data', new_callable=AsyncMock) as load, \
                    patch.object(hello, 'sendAudioMessage', new_callable=AsyncMock) as send, \
                    patch.object(hello, '_wakeup_response_lock', NS(locked=lambda: True)):
                self.assertTrue(await hello.checkWakeupWords(conn, '小兰小兰'))
            load.assert_not_awaited()
            conn.tts.to_tts.assert_called_once_with('我在呢，你说。')
            self.assertEqual([b'current-voice'], send.await_args_list[0].args[2])

    async def test_matching_voice_cache_is_reused(self):
        conn = self.context()
        response = dict(voice='selected-voice', file_path='selected.wav', text='我在呢', time=0)
        with patch.object(hello.wakeup_words_config, 'get_wakeup_response', return_value=response), \
                patch.object(hello, 'audio_to_data', new_callable=AsyncMock, return_value=[b'cached']) as load, \
                patch.object(hello, 'sendAudioMessage', new_callable=AsyncMock), \
                patch.object(hello, '_wakeup_response_lock', NS(locked=lambda: True)):
            self.assertTrue(await hello.checkWakeupWords(conn, '小兰小兰'))
        conn.tts.to_tts.assert_not_called()
        load.assert_awaited_once_with('selected.wav', use_cache=False)

    async def test_failed_synthesis_does_not_play_default_voice(self):
        conn = self.context()
        conn.tts.to_tts.side_effect = TimeoutError
        with patch.object(hello.wakeup_words_config, 'get_wakeup_response', return_value=None), \
                patch.object(hello, 'sendAudioMessage', new_callable=AsyncMock) as send:
            self.assertFalse(await hello.checkWakeupWords(conn, '小兰小兰'))
        send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
