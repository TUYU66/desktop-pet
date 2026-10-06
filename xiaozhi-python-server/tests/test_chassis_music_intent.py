"""Pure intent regression cases; no hardware, network or music playback."""
import unittest
from core.device.chassis_intent import classify_chassis_music_request, parse_chassis_music_request, classify_direct_chassis_request


class ChassisMusicIntentTests(unittest.TestCase):
    def test_bluetooth_mode_requests_include_short_spoken_request(self):
        for text in ('蓝牙控制', '小兰，蓝牙控制', '进入蓝牙控制', '连接蓝牙控制', '打开手机遥控'):
            self.assertEqual(classify_direct_chassis_request(text), 'self_chassis_bluetooth_on')
        for text in ('关闭蓝牙控制', '退出蓝牙控制', '请关闭手机遥控', '断开连接',
                     '小兰，断开连接吧', '断开蓝牙连接', '断开蓝牙控制', '关闭蓝牙'):
            self.assertEqual(classify_direct_chassis_request(text), 'self_chassis_bluetooth_off')
        for text in ('不要蓝牙控制', '怎么开启蓝牙控制', '如果打开手机遥控', '他说“蓝牙控制”',
                     '不要断开连接', '怎么断开连接', '断开网络连接', '断开连接然后起立',
                     '他说“断开连接”', '如果断开蓝牙连接'):
            self.assertIsNone(classify_direct_chassis_request(text))

    def test_explicit_sequences(self):
        for text in ("站起来播放音乐", "请站起来，然后播放音乐", "起立再放首歌"):
            self.assertEqual(classify_chassis_music_request(text), "self_chassis_stand_up")
        for text in ("坐下然后播放音乐", "坐下放歌", "休息一下再放音乐"):
            self.assertEqual(classify_chassis_music_request(text), "self_chassis_rest")

    def test_does_not_invent_motion(self):
        for text in ("不要站起来播放音乐", "如果站起来播放音乐", "站起来不要播放音乐",
                     "为什么站起来播放音乐", "播放音乐", "左转播放音乐",
                     "他说“站起来播放音乐”", "站起来播放音乐然后坐下"):
            self.assertIsNone(classify_chassis_music_request(text))

    def test_resume_sequences_preserve_resume_instead_of_starting_another_song(self):
        for text in ('站起来然后继续播放', '请站起来，然后继续播放音乐', '起立再继续听歌',
                     '站起来并恢复播放', '站起来继续音乐'):
            self.assertEqual(parse_chassis_music_request(text), ('self_chassis_stand_up', 'resume'))
        self.assertEqual(parse_chassis_music_request('坐下然后继续播放'), ('self_chassis_rest', 'resume'))
        self.assertEqual(parse_chassis_music_request('站起来然后播放音乐'), ('self_chassis_stand_up', 'play'))

    def test_resume_sequences_do_not_consume_negations_or_extra_requests(self):
        for text in ('不要站起来然后继续播放', '站起来然后不要继续播放', '如果站起来然后继续播放',
                     '他说“站起来然后继续播放”', '为什么站起来然后继续播放',
                     '站起来然后继续播放然后坐下', '站起来然后继续播放晴天'):
            self.assertIsNone(parse_chassis_music_request(text))
