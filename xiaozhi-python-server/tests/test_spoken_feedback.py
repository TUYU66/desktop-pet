"""Spoken prompts must preserve receipt truth and genuine starvation warnings."""
import unittest
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from core.conversation.feedback import (reminder_announcement, chassis_acknowledgement,
                                 chassis_outcome, creation_feedback, spoken_date,
                                 music_feedback, failure_feedback)
from core.handle.sendAudioHandle import _do_send_audio


class SpokenFeedbackTests(unittest.TestCase):
    def test_repeat_notice_and_single_notice_keep_content_without_duplicate_punctuation(self):
        first=reminder_announcement({'text':'吃饭。','deliveryNumber':1})
        retry=reminder_announcement({'text':'吃饭。','deliveryNumber':2})
        self.assertTrue(first.startswith('到时间啦，吃饭。'))
        self.assertTrue(retry.startswith('再提醒你一下，吃饭。'))
        self.assertNotIn('。。',first)
        self.assertNotIn('第1条',first)
        batch=reminder_announcement({'text':'吃饭','deliveryNumber':3},0,2)
        self.assertTrue(batch.startswith('还有两件事等你回应。'))

    def test_actions_only_rephrase_verified_completions(self):
        name='self_chassis_turn_around'
        self.assertEqual('好，转个身。',chassis_acknowledgement(name))
        self.assertEqual('好，转过来了。',chassis_outcome(name,'好，我转过身来啦。'))
        for raw in ('转向指令已发送。','我没收到转向完成确认，请先看看我的状态，别连续重试。'):
            self.assertIn('没确认',chassis_outcome(name,raw))
            self.assertNotIn('转过来了',chassis_outcome(name,raw))
        self.assertEqual('底盘仍离线。',chassis_outcome(name,'底盘仍离线。'))
        self.assertEqual('好，坐下来歇会儿。',chassis_acknowledgement('self_chassis_rest','坐下'))

    @staticmethod
    def ms(value):
        return int(datetime.fromisoformat(value+'+08:00').timestamp()*1000)

    def test_creation_is_concise_but_explicit_seconds_and_cross_year_stay_precise(self):
        now=self.ms('2026-10-05T17:58:26')
        body=dict(title='去超市',triggerAt=now+60000,recurrence='once')
        result=dict(current=dict(title='去超市',scheduledAt=body['triggerAt'],status='scheduled'))
        self.assertEqual('好，今天下午5点59分提醒你去超市。',creation_feedback(result,body,now))
        self.assertIn('59分26秒',creation_feedback(result,body,now,include_seconds=True))
        self.assertIn('已经存好了',creation_feedback(result,body,now,checking=True))
        self.assertIn('2027年1月1日',spoken_date(self.ms('2027-01-01T12:00:00'),self.ms('2026-12-31T12:00:00')))

    def test_recovered_terminal_states_never_promise_another_reminder(self):
        now=self.ms('2026-10-05T18:00:00')
        body=dict(title='去超市',triggerAt=now-60000,recurrence='once')
        for status,phrase in [('completed','记为收到了'),('cancelled','取消了'),
                ('expired','不再自动提醒'),('history_removed','清理了')]:
            with self.subTest(status=status):
                result=dict(current=dict(title='去超市',scheduledAt=body['triggerAt'],status=status))
                original=deepcopy((result,body))
                answer=creation_feedback(result,body,now,checking=True)
                self.assertIn(phrase,answer)
                self.assertNotIn('提醒你去超市',answer)
                self.assertNotIn('好，',answer)
                self.assertEqual(original,(result,body))

    def test_recovery_describes_subsequent_edit_and_paused_plan_truthfully(self):
        now=self.ms('2026-10-05T11:00:00')
        body=dict(title='去超市',triggerAt=now+3600000,recurrence='once')
        result=dict(current=dict(title='买菜',scheduledAt=now+7200000,status='scheduled'))
        answer=creation_feedback(result,body,now,checking=True)
        self.assertIn('之前设的是今天中午12点整提醒你去超市',answer)
        self.assertIn('现在是今天下午1点整提醒你买菜',answer)
        body['recurrence']='daily'
        result=dict(current=dict(title='去超市',initialAt=body['triggerAt'],recurrence='daily',status='paused'))
        answer=creation_feedback(result,body,now,checking=True)
        self.assertIn('现在暂停了',answer)
        self.assertNotIn('第一次是',answer)

    def test_music_feedback_preserves_shuffle_identity_and_choice_uncertainty(self):
        result=dict(name='孙燕姿 - 我不难过',playlistName='我的红心歌单',mode='shuffle')
        original=deepcopy(result)
        answer=music_feedback('play',result)
        self.assertIn('随机放你的《我的红心歌单》',answer)
        self.assertIn('孙燕姿 - 我不难过',answer)
        self.assertNotIn('已经播放',answer)
        self.assertEqual(result,original)
        result.update(title='我不难过',artist='孙燕姿')
        self.assertEqual('好，接着放孙燕姿的《我不难过》。',music_feedback('resume',result))
        raw='暂未找到普通原曲，查到这些网易云版本：1，歌手 - 歌曲 (Live)。请说播放第几首，或在网页选择。'
        answer=failure_feedback(raw)
        self.assertIn('还没找到原曲',answer)
        self.assertIn('(Live)',answer)
        self.assertNotIn('接下来放',answer)


class AudioGapTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_music_transition_does_not_hide_later_starvation(self):
        logger=Mock()
        conn=NS(logger=logger,conn_from_mqtt_gateway=False,websocket=NS(send=AsyncMock()))
        flow={'sentence_id':'reminder','packet_count':3,'buffered_until':1,
              'expected_gap':'reminder_music_start'}
        with patch('core.handle.sendAudioHandle.time.monotonic',side_effect=[2,4]):
            await _do_send_audio(conn,b'first-music-frame',flow)
            await _do_send_audio(conn,b'late-next-frame',flow)
        logger.bind.return_value.debug.assert_called_once()
        logger.bind.return_value.warning.assert_called_once()
        self.assertEqual('continuous_audio',logger.bind.return_value.warning.call_args.args[-1])
        self.assertNotIn('expected_gap',flow)
        self.assertEqual(2,conn.websocket.send.await_count)
        self.assertEqual(5,flow['packet_count'])


if __name__=='__main__':
    unittest.main()
