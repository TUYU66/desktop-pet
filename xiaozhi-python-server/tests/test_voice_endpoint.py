"""Audio-time endpointing must tolerate network jitter and retain short commands."""
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType
from core.providers.vad.silero import VADProvider


class RecognitionProvider(ASRProviderBase):
    async def speech_to_text(self, *args, **kwargs):
        return "", None


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.vad = VADProvider.__new__(VADProvider)
        self.vad.vad_threshold = 0.5
        self.vad.vad_threshold_low = 0.3
        self.vad.silence_threshold_ms = 650
        self.vad.frame_window_threshold = 3
        self.vad.session = Mock()
        self.conn = NS(
            client_listen_mode="realtime", client_audio_buffer=bytearray(),
            client_voice_window=deque(maxlen=5), client_have_voice=False,
            client_voice_stop=False, last_is_voice=False, vad_last_voice_time=0,
            _vad_opus_decoder=NS(decode=lambda *args: bytes(512 * 2)),
        )

    def frame(self, probability):
        self.vad.session.run.return_value = (
            np.array([[probability]], dtype=np.float32),
            np.zeros((2, 1, 128), dtype=np.float32),
        )
        return self.vad.is_vad(self.conn, b"one decoded 32ms frame")

    def begin_speech(self):
        for _ in range(3):
            self.frame(0.9)
        self.assertTrue(self.conn.client_have_voice)

    def test_processing_delay_does_not_cut_continuing_audio(self):
        with patch("core.providers.vad.silero.time.time", return_value=100):
            self.begin_speech()
        # Transport takes several seconds, but only 96ms of audio is silent.
        with patch("core.providers.vad.silero.time.time", return_value=110):
            for _ in range(3):
                self.frame(0.0)
        self.assertFalse(self.conn.client_voice_stop)

    def test_buffered_silence_ends_even_when_packets_arrive_in_one_burst(self):
        with patch("core.providers.vad.silero.time.time", return_value=100):
            self.begin_speech()
            for _ in range(25):
                self.frame(0.0)
        self.assertTrue(self.conn.client_voice_stop)

    def test_normal_pause_inside_sentence_does_not_end_turn(self):
        self.begin_speech()
        for _ in range(10):
            self.frame(0.0)
        self.assertFalse(self.conn.client_voice_stop)
        for _ in range(3):
            self.frame(0.9)
        self.assertEqual(self.conn._vad_silence_samples, 0)
        self.assertFalse(self.conn.client_voice_stop)


class ShortUtteranceTests(unittest.IsolatedAsyncioTestCase):
    async def test_vad_confirmed_short_command_reaches_asr(self):
        provider = RecognitionProvider()
        provider.interface_type = InterfaceType.LOCAL
        provider.queue_utterance = Mock()
        conn = NS(client_listen_mode="realtime", asr=provider,
                  asr_audio=[b"onset"], client_have_voice=True,
                  client_voice_stop=True)
        conn.reset_audio_states = lambda: conn.asr_audio.clear()
        await provider.receive_audio(conn, b"end", False)
        provider.queue_utterance.assert_called_once_with(conn, [b"onset", b"end"])
