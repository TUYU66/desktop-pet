import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from core.reminders.parser import parse_time,title_from,ZONE
from core.reminders.conversation import respond,relevant
from core.reminders.music import MusicWait,interrupt_music,melody_packets
from test_reminder_handler import connection


def ms(value): return int(datetime.fromisoformat(value).replace(tzinfo=ZONE).timestamp()*1000)
NOW=ms('2026-09-14T10:00:00')


class TimeTests(unittest.TestCase):
    def test_common_dates(self):
        for text,expected in [('明天下午三点','2026-09-15T15:00:00'),('后天晚上八点','2026-09-16T20:00:00'),('9月20号早上八点半','2026-09-20T08:30:00'),('2027年1月1日凌晨零点','2027-01-01T00:00:00'),('下周一上午八点','2026-09-21T08:00:00')]:
            with self.subTest(text=text): self.assertEqual((ms(expected),'once',None),parse_time(text,NOW))
    def test_missing_time_and_ambiguous_period_never_guessed(self):
        for text in ('明天','明天下午','明天三点','明天上午再提醒','9月20号早上','每周晚上八点'):
            with self.subTest(text=text): self.assertIsNone(parse_time(text,NOW)[0])
    def test_invalid_or_past_dates_do_not_roll_year(self):
        for text in ('2月30号上午十点','9月13号上午十点','今天早上八点','明天晚上25点','明天14:99'):
            with self.subTest(text=text): self.assertIsNone(parse_time(text,NOW)[0])
    def test_relative_time_fixed_to_turn(self):
        self.assertEqual(NOW+600000,parse_time('十分钟后提醒我喝水',NOW)[0])
        self.assertEqual(NOW+1800000,parse_time('半小时后',NOW)[0])
    def test_recurring_next_day_and_weekend(self):
        self.assertEqual((ms('2026-09-15T08:00:00'),'daily',None),parse_time('每天早上八点',NOW))
        self.assertEqual((ms('2026-09-18T20:00:00'),'weekly',None),parse_time('每周五晚上八点',NOW))
        self.assertEqual(ms('2026-09-21T08:00:00'),parse_time('工作日早上八点',ms('2026-09-18T10:00:00'))[0])
    def test_followup_last_time_wins(self):
        self.assertEqual(ms('2026-09-15T15:00:00'),parse_time('明天三点，下午三点',NOW)[0])
        self.assertEqual(ms('2026-09-16T15:00:00'),parse_time('明天下午三点，后天下午三点',NOW)[0])
    def test_title_keeps_user_content(self):
        self.assertEqual('拿快递',title_from('明天下午三点提醒我拿快递'))
        self.assertEqual('拿快递',title_from('提醒我明天下午三点拿快递'))
        self.assertEqual('睡觉',title_from('每天晚上十一点提醒我睡觉'))
        self.assertEqual('看《明天会更好》',title_from('明天下午三点提醒我看《明天会更好》'))
    def test_hypothetical_is_not_command(self):
        self.assertFalse(relevant('如果我说明天下午三点提醒我喝水会怎样'))


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn=NS(headers={'device-id':'target'},logger=Mock())
        self.snapshot={'items':[],'plans':[],'serverNow':NOW}
        self.state=patch('core.reminders.client.state',new=AsyncMock(return_value=self.snapshot)); self.state.start(); self.addCleanup(self.state.stop)
        self.commands=AsyncMock(side_effect=self.receipt)
        p=patch('core.device.face.send_face',AsyncMock()); p.start(); self.addCleanup(p.stop)
        self.p=patch('core.reminders.client.command',self.commands); self.p.start(); self.addCleanup(self.p.stop)
    def receipt(self,device,body):
        if body['action']=='create':
            return dict(id=body['requestId'],creation=dict(requestId=body['requestId'],deviceId=device,title=body['title'],triggerAt=body['triggerAt'],recurrence=body['recurrence'],seconds=None),current=dict(status='scheduled' if body['recurrence']=='once' else 'active'))
        if body['action'].startswith('plan_'): return dict(id=body['id'])
        return dict(id=body['id'],status='completed' if body['action']=='confirm' else 'scheduled',version=body['version']+1,dueAt=body.get('triggerAt',NOW+600000))
    async def test_incomplete_create_then_followup_saves_only_once(self):
        self.assertIn('几点',await respond(self.conn,'明天提醒我拿快递'))
        self.commands.assert_not_awaited()
        self.assertIn('提醒你',await respond(self.conn,'下午三点'))
        self.assertEqual(ms('2026-09-15T15:00:00'),self.commands.call_args.args[1]['triggerAt'])
        self.assertEqual('拿快递',self.commands.call_args.args[1]['title'])
    async def test_failed_create_retry_reuses_id_and_time(self):
        calls=0
        def retry(device,body):
            nonlocal calls
            calls+=1
            if calls==1: raise TimeoutError()
            return self.receipt(device,body)
        self.commands.side_effect=retry
        self.assertIn('我还没确认',await respond(self.conn,'十分钟后提醒我喝水'))
        self.assertIn('提醒你',await respond(self.conn,'再试一次'))
        first,second=[c.args[1] for c in self.commands.call_args_list]
        self.assertEqual(first,second)
    async def test_title_dates_cannot_change_trigger_date(self):
        await respond(self.conn,'后天下午三点提醒我看《明天》')
        self.assertEqual(ms('2026-09-16T15:00:00'),self.commands.call_args.args[1]['triggerAt'])
    async def test_confirmation_uses_current_device_and_version(self):
        self.conn.llm=Mock()
        self.conn.llm.response_json.return_value='{"action":"confirm","targetId":"one"}'
        self.snapshot['items']=[dict(id='one',title='喝水',status='awaiting_confirmation',version=8)]
        self.assertIn('记为收到了',await respond(self.conn,'知道了'))
        self.assertEqual(('target',dict(action='confirm',id='one',version=8)),self.commands.call_args.args)
    async def test_multiple_reminders_require_selection(self):
        self.conn.llm=Mock()
        self.conn.llm.response_json.side_effect=['{"action":"ambiguous","targetId":null}', '{"action":"confirm","targetId":"2"}']
        self.snapshot['items']=[dict(id=str(i),title=f'事项{i}',status='awaiting_confirmation',version=8) for i in (1,2)]
        self.assertIn('哪一条',await respond(self.conn,'知道了'))
        self.commands.assert_not_awaited()
        await respond(self.conn,'第二条')
        self.assertEqual('2',self.commands.call_args.args[1]['id'])
    async def test_snooze_ambiguous_time_requires_followup(self):
        self.snapshot['items']=[dict(id='one',title='喝水',status='awaiting_confirmation',version=8)]
        self.assertIn('几点',await respond(self.conn,'明天上午再提醒我'))
        self.commands.assert_not_awaited()
        await respond(self.conn,'上午九点')
        self.assertEqual(ms('2026-09-15T09:00:00'),self.commands.call_args.args[1]['triggerAt'])
    async def test_unrelated_reply_does_not_confirm(self):
        self.assertIsNone(await respond(self.conn,'今天的天气怎么样'))
        self.commands.assert_not_awaited()
    async def test_short_relative_reply_after_wake_snoozes(self):
        import time
        self.conn.reminder_response_until=time.monotonic()+60
        self.snapshot['items']=[dict(id='one',title='喝水',status='awaiting_confirmation',version=8)]
        await respond(self.conn,'半小时后')
        self.assertEqual(NOW+1800000,self.commands.call_args.args[1]['triggerAt'])
    async def test_plan_scope_does_not_cancel_occurrence(self):
        self.snapshot['plans']=[dict(id='plan',title='喝水',status='active',version=3)]
        await respond(self.conn,'取消整个喝水周期计划')
        self.assertEqual('plan_cancel',self.commands.call_args.args[1]['action'])


class ScheduleExitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        catchup = patch('core.reminders.client.catchup', new=AsyncMock(return_value={'items': [], 'count': 0}))
        catchup.start()
        self.addCleanup(catchup.stop)

    async def test_web_only_success_returns_to_standby_and_allows_reminder(self):
        import queue
        from core.reminders.conversation import handle_sync
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        from core.api.reminder_handler import ReminderHandler
        for initially_returning in (False,True):
            with self.subTest(initially_returning=initially_returning):
                conn=connection(); conn.standby=False; conn.return_to_standby=initially_returning; conn.close_after_chat=False
                conn.chat_input_source='web'; conn.conversation_awake=False
                conn.loop=asyncio.get_running_loop(); conn.headers={'device-id':'target'}
                conn.tts.tts_text_queue=queue.Queue()
                with patch('core.reminders.client.state',new=AsyncMock(return_value={'items':[],'plans':[],'serverNow':NOW})),patch('core.reminders.client.command',new=AsyncMock(side_effect=lambda device,body:dict(id=body['requestId'],creation=dict(requestId=body['requestId'],deviceId=device,title=body['title'],triggerAt=body['triggerAt'],recurrence=body['recurrence'],seconds=None),current=dict(status='scheduled')))):
                    self.assertTrue(await asyncio.to_thread(handle_sync,conn,'10秒后提醒我出门'))
                self.assertTrue(conn.return_to_standby)
                messages=list(conn.tts.tts_text_queue.queue)
                self.assertIn('提醒你出门',messages[1].content_detail)
                self.assertIn('北京时间',messages[1].content_detail)
                await sendAudioMessage(conn,SentenceType.LAST,[],None,conn.sentence_id)
                self.assertTrue(conn.standby)
                api=ReminderHandler(NS(device_handlers={'target':conn}))
                self.assertEqual('sent',await api.deliver('target','出门','next-attempt'))

    async def test_voice_time_question_keeps_listening(self):
        import queue
        from core.reminders.conversation import handle_sync
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        conn=connection(); conn.standby=False; conn.return_to_standby=True; conn.close_after_chat=False
        conn.conversation_awake=True
        conn.loop=asyncio.get_running_loop(); conn.headers={'device-id':'target'}
        conn.tts.tts_text_queue=queue.Queue()
        with patch('core.reminders.client.state',new=AsyncMock(return_value={'items':[],'plans':[],'serverNow':NOW})),patch('core.reminders.client.command',new=AsyncMock()) as command:
            self.assertTrue(await asyncio.to_thread(handle_sync,conn,'明天提醒我出门'))
            command.assert_not_awaited()
        self.assertFalse(conn.return_to_standby)
        await sendAudioMessage(conn,SentenceType.LAST,[],None,conn.sentence_id)
        self.assertFalse(conn.standby)

    async def test_completed_schedule_retains_wake_from_both_chat_sources(self):
        import queue
        from core.reminders.conversation import handle_sync
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        for source in (None, 'web'):
            conn=connection(); conn.standby=False; conn.return_to_standby=True; conn.close_after_chat=False
            conn.conversation_awake=True; conn.chat_input_source=source
            conn.schedule_operation_complete=True; conn.loop=asyncio.get_running_loop()
            conn.headers={'device-id':'target'}
            conn.tts.tts_text_queue=queue.Queue()
            with patch('core.reminders.conversation.respond',new=AsyncMock(return_value='已设置明天中午的提醒')):
                self.assertTrue(await asyncio.to_thread(handle_sync,conn,'明天中午提醒我拿快递'))
            self.assertFalse(conn.return_to_standby)
            await sendAudioMessage(conn,SentenceType.LAST,[],None,conn.sentence_id)
            self.assertFalse(conn.standby)
            self.assertTrue(conn.conversation_awake)

    async def test_web_time_question_returns_to_standby(self):
        import queue
        from core.reminders.conversation import handle_sync
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        conn=connection(); conn.standby=False; conn.return_to_standby=True; conn.close_after_chat=False
        conn.loop=asyncio.get_running_loop(); conn.headers={'device-id':'target'}
        conn.chat_input_source='web'
        conn.tts.tts_text_queue=queue.Queue()
        with patch('core.reminders.client.state',new=AsyncMock(return_value={'items':[],'plans':[],'serverNow':NOW})),patch('core.reminders.client.command',new=AsyncMock()) as command:
            self.assertTrue(await asyncio.to_thread(handle_sync,conn,'明天提醒我出门'))
            command.assert_not_awaited()
        self.assertTrue(conn.return_to_standby)
        await sendAudioMessage(conn,SentenceType.LAST,[],None,conn.sentence_id)
        self.assertTrue(conn.standby)


class MusicTests(unittest.IsolatedAsyncioTestCase):
    async def test_volume_scales_music_pcm_and_zero_is_silent(self):
        import array
        melody_packets.cache_clear()
        captured=[]
        def encode(raw,*args,**kwargs): captured.append(max(abs(x) for x in array.array('h',raw)))
        try:
            with patch('core.utils.util.pcm_to_data_stream',side_effect=encode):
                self.assertEqual([],melody_packets(0))
                melody_packets(30); melody_packets(100)
            self.assertEqual(2,len(captured))
            self.assertGreater(captured[1],3*captured[0])
            self.assertLess(captured[1],32767)
            with self.assertRaises(ValueError): melody_packets(101)
        finally: melody_packets.cache_clear()
    async def test_playing_music_stops_on_web_change_with_lock_cleanup(self):
        conn=connection(); conn.chat_lock.acquire(); conn.sentence_id='attempt'; conn.standby=False
        music=MusicWait(conn,'target','id','attempt')
        snapshots=[{'serverNow':NOW,'items':[dict(id='id',attemptId='attempt',status='awaiting_confirmation',ackDeadline=NOW+60000)]},
                   {'serverNow':NOW+1000,'items':[dict(id='id',attemptId=None,status='completed')]}]
        with patch('core.reminders.music.melody_packets',return_value=[b'opus']*30),patch('core.reminders.client.state',new=AsyncMock(side_effect=snapshots)):
            await asyncio.wait_for(music.run(),3)
        self.assertTrue(any(isinstance(c.args[0],bytes) for c in conn.websocket.send.call_args_list))
        self.assertFalse(conn.chat_lock.locked()); self.assertTrue(conn.standby)
    async def test_web_confirmation_stops_without_sending_music(self):
        conn=connection(); conn.chat_lock.acquire(); conn.sentence_id='attempt'
        music=MusicWait(conn,'target','id','attempt')
        with patch('core.reminders.music.melody_packets',return_value=[b'a']),patch('core.reminders.client.state',new=AsyncMock(return_value={'serverNow':NOW,'items':[dict(id='id',attemptId='attempt',status='completed')]})):
            await music.run()
        self.assertFalse(conn.chat_lock.locked()); self.assertTrue(conn.standby)
        self.assertFalse(any(isinstance(c.args[0],bytes) for c in conn.websocket.send.call_args_list))
    async def test_immediate_wake_cancellation_releases_lock_once(self):
        conn=connection(); conn.chat_lock.acquire()
        music=MusicWait(conn,'target','id','attempt'); conn.reminder_music=music
        music.task=asyncio.create_task(music.run())
        with patch('core.reminders.client.command',new=AsyncMock()) as cmd:
            await interrupt_music(conn)
            self.assertEqual('listening',cmd.call_args.args[1]['action'])
        self.assertFalse(conn.chat_lock.locked()); self.assertFalse(conn.return_to_standby)
    async def test_music_asset_encodes_with_real_opus(self):
        packets=await asyncio.to_thread(melody_packets)
        self.assertGreater(len(packets),100)
        self.assertTrue(all(isinstance(x,bytes) and len(x)>0 for x in packets))
