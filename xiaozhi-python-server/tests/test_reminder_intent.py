import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from core.reminders.intent import classify
from core.reminders.conversation import respond


class ReminderIntentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item = dict(id='r1',title='休息',status='awaiting_confirmation',version=3)
        self.conn = SimpleNamespace(llm=Mock(), headers={'device-id':'test'}, logger=Mock(), reminder_response_until=time.monotonic()+90)

    async def test_unknown_target_rejected(self):
        self.conn.llm.response_json.return_value='{"action":"confirm","targetId":"other"}'
        with self.assertRaises(ValueError):
            await classify(self.conn,'收到',[self.item])

    async def test_semantic_reply_executes_validated_target(self):
        self.conn.llm.response_json.return_value=json.dumps({'action':'confirm','targetId':'r1'})
        with patch('core.reminders.client.state',AsyncMock(return_value={'serverNow':0,'items':[self.item],'plans':[]})), patch('core.reminders.client.command',AsyncMock(return_value={})) as command, patch('core.device.face.send_face',AsyncMock()):
            answer=await respond(self.conn,'这就去休息，谢谢你')
            self.assertIn('提醒结束',answer)
            command.assert_awaited_once_with('test',{'action':'confirm','id':'r1','version':3})

    async def test_negative_and_unrelated_never_use_keyword_confirm(self):
        self.conn.llm.response_json.return_value='{"action":"none","targetId":null}'
        with patch('core.reminders.client.state',AsyncMock(return_value={'serverNow':0,'items':[self.item],'plans':[]})), patch('core.reminders.client.command',AsyncMock()) as command:
            for text in ('我还没好了','你说知道了是什么意思','外面天气怎么样'):
                self.assertIsNone(await respond(self.conn,text))
            command.assert_not_awaited()

    async def test_model_failure_does_not_close(self):
        self.conn.llm.response_json.side_effect=ValueError('bad json')
        with patch('core.reminders.client.state',AsyncMock(return_value={'serverNow':0,'items':[self.item],'plans':[]})), patch('core.reminders.client.command',AsyncMock()) as command:
            self.assertIn('保持不变',await respond(self.conn,'知道了'))
            command.assert_not_awaited()

    async def test_snooze_is_not_confirmation(self):
        self.conn.llm.response_json.return_value='{"action":"snooze","targetId":"r1"}'
        with patch('core.reminders.client.state',AsyncMock(return_value={'serverNow':0,'items':[self.item],'plans':[]})), patch('core.reminders.client.command',AsyncMock(return_value={'dueAt':600000})) as command:
            await respond(self.conn,'知道了，十分钟后再提醒')
            self.assertEqual(command.await_args.args[1]['action'],'snooze')


if __name__ == '__main__': unittest.main()
