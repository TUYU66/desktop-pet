"""提醒HTTP与真实音频发送函数的隔离回归，不访问模型、TTS服务或硬件。"""
import asyncio
import json
import threading
import unittest
import uuid
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from core.api.reminder_handler import ReminderHandler


def request(body=None,remote="127.0.0.1",key="xiaozhi-reminders"):
    return NS(remote=remote,headers={"Service-Key":key},json=AsyncMock(return_value=body))


def connection():
    conn=NS(stop_event=threading.Event(),chat_lock=threading.Lock(),standby=True,
        client_is_speaking=False,client_abort=False,sentence_id="old",session_id="synthetic-session",features={"standby_connection":True},
        config={},conn_from_mqtt_gateway=False,websocket=NS(send=AsyncMock()),logger=Mock(),
        tts=NS(to_tts=Mock(return_value=[b"synthetic-opus"]),tts_audio_first_sentence=True),reset_audio_states=Mock())
    conn.clearSpeakStatus=lambda: setattr(conn,"client_is_speaking",False)
    return conn


class ReminderHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env=patch.dict("os.environ",{"XIAOZHI_REMINDER_SERVICE_KEY":""}); self.env.start(); self.addCleanup(self.env.stop)
        self.conn=connection(); self.api=ReminderHandler(NS(device_handlers={"target":self.conn}))

    async def test_default_service_key_requires_loopback(self):
        response=await self.api.handle_announce(request({},remote="192.168.1.5"))
        self.assertEqual(401,response.status)
        self.assertTrue(self.api.authorized(request(remote="::1")))
        self.assertTrue(self.api.authorized(request(remote="::ffff:127.0.0.1")))
        with patch.dict("os.environ",{"XIAOZHI_REMINDER_SERVICE_KEY":"private-test-key"}):
            self.assertTrue(self.api.authorized(request(remote="192.168.1.5",key="private-test-key")))
            self.assertFalse(self.api.authorized(request(remote="192.168.1.5")))

    async def test_invalid_input_does_not_dispatch(self):
        self.api.deliver=AsyncMock()
        for body in ([],{},dict(attemptId="invalid",deviceId="target",text="喝水"),
                     dict(attemptId=str(uuid.uuid4()),deviceId="target",text="x"*121)):
            self.assertEqual(400,(await self.api.handle_announce(request(body))).status)
        self.api.deliver.assert_not_awaited()

    async def test_duplicate_attempt_shares_task_and_rejects_changed_payload(self):
        async def deliver(*_): await asyncio.sleep(0.01); return "sent"
        self.api.deliver=AsyncMock(side_effect=deliver)
        body=dict(attemptId=str(uuid.uuid4()),deviceId="target",text="喝水")
        results=await asyncio.gather(self.api.handle_announce(request(body)),self.api.handle_announce(request(body)))
        self.assertTrue(all(json.loads(r.text)["outcome"]=="sent" for r in results))
        self.api.deliver.assert_awaited_once()
        self.assertEqual(409,(await self.api.handle_announce(request({**body,"text":"别的内容"}))).status)

    async def test_offline_device_does_not_fall_back(self):
        self.assertEqual("retry",await self.api.deliver("missing","喝水","attempt"))
        self.conn.tts.to_tts.assert_not_called()

    async def test_active_conversation_and_held_lock_are_not_interrupted(self):
        self.conn.standby=False
        self.assertEqual("retry",await self.api.deliver("target","喝水","attempt"))
        self.conn.standby=True; self.conn.chat_lock.acquire()
        self.assertEqual("retry",await self.api.deliver("target","喝水","attempt"))
        self.assertTrue(self.conn.chat_lock.locked()); self.conn.chat_lock.release()
        self.conn.tts.to_tts.assert_not_called()

    async def test_audio_is_sent_directly_and_returns_to_standby(self):
        outcome=await self.api.deliver("target","喝水","attempt")
        self.assertEqual("sent",outcome)
        messages=[c.args[0] for c in self.conn.websocket.send.await_args_list]
        self.assertIn(b"synthetic-opus",messages)
        controls=[json.loads(m) for m in messages if isinstance(m,str)]
        self.assertEqual("start",controls[0]["state"])
        self.assertFalse(any(m["type"]=="stt" for m in controls))
        self.assertEqual("standby",controls[-1]["type"])
        self.assertTrue(self.conn.standby); self.assertFalse(self.conn.chat_lock.locked())
        self.assertFalse(self.conn.client_is_speaking)

    async def test_retry_during_song_preparation_does_not_cancel_it(self):
        music=NS(state='loading',preparing=NS(task=Mock()),halt=AsyncMock(),cancel_preparation=AsyncMock())
        self.conn.local_music=music
        self.assertEqual('retry',await self.api.deliver('target','喝水','attempt'))
        music.halt.assert_not_awaited(); music.cancel_preparation.assert_not_awaited()
        self.conn.tts.to_tts.assert_not_called()

    async def test_user_conversation_and_speech_do_not_silence_music(self):
        music=NS(state='playing',preparing=None,task=NS(done=lambda:False),stream_id='music',
            interrupted_for_chat=False,halt=AsyncMock(),cancel_preparation=AsyncMock())
        self.conn.local_music=music
        self.conn.standby=False; self.conn.client_is_speaking=True
        self.assertEqual('retry',await self.api.deliver('target','喝水','attempt'))
        self.conn.sentence_id='music'; self.conn.client_have_voice=True
        self.assertEqual('retry',await self.api.deliver('target','喝水','attempt'))
        music.halt.assert_not_awaited(); music.cancel_preparation.assert_not_awaited()
        self.conn.tts.to_tts.assert_not_called()

    async def test_eligible_music_is_paused_then_reminder_is_sent(self):
        music=NS(state='playing',preparing=None,task=NS(done=lambda:False),stream_id='music',
            interrupted_for_chat=False,lock=asyncio.Lock(),halt=AsyncMock(),speech_finished=Mock())
        self.conn.local_music=music
        self.conn.sentence_id='music'; self.conn.standby=False; self.conn.client_is_speaking=True
        self.conn.chat_lock.acquire()
        async def halt(**kwargs):
            self.conn.standby=True; self.conn.client_is_speaking=False
            music.state='paused'; music.stream_id=None; self.conn.chat_lock.release()
        music.halt.side_effect=halt
        self.assertEqual('sent',await self.api.deliver('target','喝水','attempt'))
        music.halt.assert_awaited_once_with(for_chat=False)
        self.assertTrue(self.conn.standby); self.assertFalse(self.conn.chat_lock.locked())

    async def test_empty_tts_is_retryable_and_releases_device(self):
        self.conn.tts.to_tts.return_value=None
        self.assertEqual("retry",await self.api.deliver("target","喝水","attempt"))
        self.assertFalse(self.conn.chat_lock.locked()); self.assertTrue(self.conn.standby)
        self.assertFalse(any(isinstance(c.args[0],bytes) for c in self.conn.websocket.send.await_args_list))

    async def test_partial_audio_failure_is_unknown_not_retryable(self):
        async def fail_audio(data):
            if isinstance(data,bytes): raise ConnectionError()
        self.conn.websocket.send.side_effect=fail_audio
        self.assertEqual("unknown",await self.api.deliver("target","喝水","attempt"))
        self.assertFalse(self.conn.chat_lock.locked()); self.assertTrue(self.conn.standby)

    async def test_interrupt_during_audio_is_not_reported_sent(self):
        async def interrupt(data):
            if isinstance(data,bytes): self.conn.client_abort=True
        self.conn.websocket.send.side_effect=interrupt
        self.assertEqual("unknown",await self.api.deliver("target","喝水","attempt"))
        self.assertFalse(self.conn.chat_lock.locked())
