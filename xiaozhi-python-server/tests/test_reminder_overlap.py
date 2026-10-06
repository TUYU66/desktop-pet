"""Overlapping reminders retain individual receipts and never compete for audio."""
import asyncio
import json
import unittest
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch
from core.api.reminder_handler import ReminderHandler
from core.reminders.music import MusicWait, handoff_music, interrupt_music
from core.providers.tts.dto.dto import SentenceType
from types import SimpleNamespace as NS
from test_reminder_handler import connection, request


def item(title):
    return dict(reminderId=str(uuid.uuid4()), attemptId=str(uuid.uuid4()), text=title, deliveryNumber=1)


class OverlapDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn=connection()
        self.api=ReminderHandler(NS(device_handlers={'target':self.conn}))
        self.items=[item('拿快递'),item('喝水')]
        self.announcements=[]
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict('os.environ',{'XIAOZHI_REMINDER_SERVICE_KEY':''}))
        self.state=self.stack.enter_context(patch('core.reminders.client.state',new=AsyncMock(return_value={
            'items':[dict(id=i['reminderId'],attemptId=i['attemptId'],status='dispatching') for i in self.items]})))
        self.command=self.stack.enter_context(patch('core.reminders.client.command',new=AsyncMock()))
        self.stack.enter_context(patch('core.api.reminder_handler._wait_for_audio_completion',new=AsyncMock()))
        self.stack.enter_context(patch('core.device.face.send_face',new=AsyncMock()))
        async def output(conn,kind,audio,text,stream):
            if kind==SentenceType.FIRST:
                self.announcements.append(text)
                if not hasattr(conn,'audio_flow_control'): conn.audio_flow_control={'packet_count':0}
                conn.audio_flow_control['packet_count']+=len(audio)
            else:
                conn.standby=True; conn.client_is_speaking=False
        self.output=self.stack.enter_context(patch('core.api.reminder_handler.sendAudioMessage',new=AsyncMock(side_effect=output)))
        async def waiting(*args): await asyncio.Event().wait()
        self.stack.enter_context(patch('core.reminders.music.MusicWait.run',new=AsyncMock(side_effect=waiting)))
        self.addAsyncCleanup(self.cleanup_music)

    async def cleanup_music(self):
        await interrupt_music(self.conn)

    async def test_two_due_reminders_have_one_introduction_and_independent_receipts(self):
        result=await self.api.deliver_batch('target',self.items,'batch-stream')
        self.assertEqual({i['attemptId']:'sent' for i in self.items},result)
        self.assertIn('你有两件事到时间了。第一件是拿快递。',self.announcements[0])
        self.assertTrue(self.announcements[1].startswith('还有喝水。'))
        self.assertEqual(1,''.join(self.announcements).count('到时间了'))
        self.assertEqual([(i['reminderId'],i['attemptId']) for i in self.items],self.conn.reminder_music.members)
        self.assertEqual(1,sum(json.loads(c.args[0]).get('state')=='start' for c in self.conn.websocket.send.await_args_list))
        start=next(json.loads(c.args[0]) for c in self.conn.websocket.send.await_args_list if json.loads(c.args[0]).get('state')=='start')
        self.assertEqual('reminder',start['media'])
        self.assertFalse(self.conn.tts.tts_audio_first_sentence)

    async def test_next_voice_is_prepared_before_first_playback_finishes(self):
        ready=asyncio.Event()
        events=[]
        generated=0
        drained=0
        async def synthesize(func,text):
            nonlocal generated
            generated+=1
            events.append(f'generate-{generated}')
            if generated==2: ready.set()
            return [b'opus']
        async def drain(conn):
            nonlocal drained
            drained+=1
            if drained==1: await asyncio.wait_for(ready.wait(),1)
            events.append(f'drain-{drained}')
        with patch('core.api.reminder_handler.asyncio.to_thread',new=AsyncMock(side_effect=synthesize)), \
             patch('core.api.reminder_handler._wait_for_audio_completion',new=AsyncMock(side_effect=drain)):
            result=await self.api.deliver_batch('target',self.items,'batch-stream')
        self.assertEqual(2,len(self.announcements))
        self.assertLess(events.index('generate-2'),events.index('drain-1'))
        self.assertEqual({i['attemptId']:'sent' for i in self.items},result)

    async def test_second_tts_failure_does_not_erase_first_sent_receipt(self):
        self.conn.tts.to_tts.side_effect=[[b'first'],None]
        result=await self.api.deliver_batch('target',self.items,'batch-stream')
        self.assertEqual('sent',result[self.items[0]['attemptId']])
        self.assertEqual('retry',result[self.items[1]['attemptId']])
        self.assertEqual([(self.items[0]['reminderId'],self.items[0]['attemptId'])],self.conn.reminder_music.members)

    async def test_user_interrupt_drops_unspoken_items_without_claiming_them_sent(self):
        async def interrupt(conn,kind,audio,text,stream):
            conn.client_abort=True
        self.output.side_effect=interrupt
        result=await self.api.deliver_batch('target',self.items,'batch-stream')
        self.assertEqual('unknown',result[self.items[0]['attemptId']])
        self.assertEqual('retry',result[self.items[1]['attemptId']])
        # Preparation may finish in advance; only the first item may reach audio.
        self.assertEqual(1,self.output.await_count)
        self.assertFalse(self.conn.chat_lock.locked())

    async def test_new_due_notice_hands_off_previous_melody_and_retains_its_members(self):
        self.conn.chat_lock.acquire(); self.conn.sentence_id='old-stream'
        self.conn.standby=False; self.conn.client_is_speaking=True
        old=MusicWait(self.conn,'target','older-id','old-stream')
        old.task=asyncio.create_task(old.run()); self.conn.reminder_music=old
        result=await self.api.deliver_batch('target',self.items,'new-stream')
        self.assertTrue(old.released)
        self.assertEqual('sent',result[self.items[0]['attemptId']])
        self.assertIn(('older-id','old-stream'),self.conn.reminder_music.members)
        self.command.assert_not_awaited()  # Handoff must not extend the earlier ACK window.

    async def test_batch_replay_shares_receipts_and_reused_member_is_rejected(self):
        self.api.deliver_batch=AsyncMock(return_value={i['attemptId']:'sent' for i in self.items})
        body=dict(attemptId=str(uuid.uuid4()),deviceId='target',items=self.items)
        results=await asyncio.gather(self.api.handle_announce(request(body)),self.api.handle_announce(request(body)))
        self.api.deliver_batch.assert_awaited_once()
        self.assertEqual(json.loads(results[0].text),json.loads(results[1].text))
        self.assertEqual(409,(await self.api.handle_announce(request(dict(body,attemptId=str(uuid.uuid4()))))).status)
        single=dict(self.items[0],deviceId='target')
        self.assertEqual(409,(await self.api.handle_announce(request(single))).status)

    async def test_invalid_duplicate_or_oversized_batch_never_dispatches(self):
        self.api.deliver_batch=AsyncMock()
        for rows in ([],[self.items[0]]*2,[item('喝水') for _ in range(5)]):
            body=dict(attemptId=str(uuid.uuid4()),deviceId='target',items=rows)
            self.assertEqual(400,(await self.api.handle_announce(request(body))).status)
        self.api.deliver_batch.assert_not_awaited()


class GroupMusicTests(unittest.IsolatedAsyncioTestCase):
    async def test_reminder_music_announces_its_phase_and_uses_music_face(self):
        conn=connection(); conn.chat_lock.acquire(); conn.sentence_id='stream'; conn.standby=False
        conn.audio_flow_control={'sentence_id':'stream','packet_count':1}
        music=MusicWait(conn,'target','a','stream')
        snapshots=[{'serverNow':1000,'items':[dict(id='a',attemptId='stream',status='awaiting_confirmation',ackDeadline=2000)]},
                   {'serverNow':2000,'items':[dict(id='a',attemptId='stream',status='awaiting_confirmation',ackDeadline=2000)]}]
        with patch('core.reminders.client.state',new=AsyncMock(side_effect=snapshots)), \
             patch('core.reminders.music.melody_packets',return_value=[]), \
             patch('core.device.face.send_face',new=AsyncMock()) as face:
            await music.run()
        phase=next(json.loads(c.args[0]) for c in conn.websocket.send.await_args_list if json.loads(c.args[0]).get('state')=='reminder_wait')
        self.assertEqual('reminder',phase['media'])
        self.assertEqual('stream',phase['stream_id'])
        face.assert_any_await(conn,'singing')
        self.assertEqual('reminder_music_start',conn.audio_flow_control['expected_gap'])
        self.assertFalse(conn.chat_lock.locked())

    async def test_confirming_one_member_does_not_stop_other_members_waiting(self):
        conn=connection(); conn.chat_lock.acquire(); conn.sentence_id='stream'; conn.standby=False
        music=MusicWait(conn,'target','a','stream',members=[('a','attempt-a'),('b','attempt-b')])
        snapshots=[{'serverNow':1000,'items':[dict(id='a',attemptId=None,status='completed'),
            dict(id='b',attemptId='attempt-b',status='awaiting_confirmation',ackDeadline=2000)]},
            {'serverNow':1100,'items':[dict(id='a',attemptId=None,status='completed'),dict(id='b',attemptId=None,status='completed')]}]
        with patch('core.reminders.client.state',new=AsyncMock(side_effect=snapshots)) as state, \
             patch('core.reminders.music.melody_packets',return_value=[]), \
             patch('core.device.face.send_face',new=AsyncMock()):
            await music.run()
            self.assertEqual(2,state.await_count)
        self.assertFalse(conn.chat_lock.locked()); self.assertTrue(conn.standby)

    async def test_previous_timeout_does_not_stop_newer_confirmation_window(self):
        conn=connection(); conn.chat_lock.acquire(); conn.sentence_id='stream'; conn.standby=False
        music=MusicWait(conn,'target','a','stream',members=[('a','attempt-a'),('b','attempt-b')])
        snapshots=[{'serverNow':2000,'items':[dict(id='a',attemptId='attempt-a',status='awaiting_confirmation',ackDeadline=1999),
            dict(id='b',attemptId='attempt-b',status='awaiting_confirmation',ackDeadline=3000)]},
            {'serverNow':3000,'items':[dict(id='b',attemptId='attempt-b',status='awaiting_confirmation',ackDeadline=3000)]}]
        with patch('core.reminders.client.state',new=AsyncMock(side_effect=snapshots)) as state, \
             patch('core.reminders.music.melody_packets',return_value=[]), \
             patch('core.device.face.send_face',new=AsyncMock()):
            await music.run()
            self.assertEqual(2,state.await_count)
        self.assertFalse(conn.chat_lock.locked())

    async def test_new_notice_cannot_override_user_wake_or_speech(self):
        conn=connection(); conn.sentence_id='stream'; conn.chat_lock.acquire(); conn.client_have_voice=True
        music=MusicWait(conn,'target','id','stream'); conn.reminder_music=music
        self.assertIsNone(await handoff_music(conn))
        self.assertFalse(music.interrupted); self.assertTrue(conn.chat_lock.locked())
        conn.chat_lock.release()

    async def test_wake_during_melody_cleanup_wins_over_the_next_notice(self):
        conn=connection(); conn.standby=False; conn.sentence_id='stream'; conn.chat_lock.acquire()
        music=MusicWait(conn,'target','id','stream'); conn.reminder_music=music
        started=asyncio.Event()
        async def running():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                conn.input_generation=1
                conn.conversation_awake=True
                music.release()
        music.task=asyncio.create_task(running())
        await asyncio.wait_for(started.wait(),1)
        self.assertIsNone(await handoff_music(conn))
        self.assertFalse(conn.standby)
        self.assertTrue(conn.conversation_awake)
        self.assertFalse(conn.chat_lock.locked())

    async def test_wake_extends_each_member_independently_not_the_whole_group(self):
        conn=connection(); conn.chat_lock.acquire(); conn.sentence_id='stream'
        music=MusicWait(conn,'target','a','stream',members=[('a','attempt-a'),('b','attempt-b')]); conn.reminder_music=music
        with patch('core.reminders.client.command',new=AsyncMock()) as command:
            await interrupt_music(conn)
            self.assertEqual({'a','b'},{call.args[1]['id'] for call in command.await_args_list})
            self.assertEqual({'attempt-a','attempt-b'},{call.args[1]['attemptId'] for call in command.await_args_list})
        self.assertFalse(conn.chat_lock.locked())
