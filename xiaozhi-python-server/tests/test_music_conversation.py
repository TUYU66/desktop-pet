"""Music routing regressions; no real device or model calls."""
import unittest
import threading
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
from core.music.conversation import parse_control, split_music_request, handle_input


class MusicCommandTests(unittest.TestCase):
    def test_playlist_command_preserves_explicit_playback_mode(self):
        for text in ('随机播放我的红心歌单  ', '网易云随机播放我的收藏歌单', '帮我随机听我的红心歌单'):
            self.assertEqual(parse_control(text), ('play', dict(source='netease', playlist_kind='liked', mode='shuffle')))
        self.assertEqual(parse_control('随机播放我的歌单《通勤》'),
            ('play', dict(source='netease', playlist_name='《通勤》', mode='shuffle')))
        self.assertEqual(parse_control('顺序播放我的红心歌单'),
            ('play', dict(source='netease', playlist_kind='liked', mode='sequence')))
        self.assertEqual(parse_control('随机播放第二个歌单'),
            ('play', dict(source='netease', playlist_name='第二个歌单', mode='shuffle')))

    def test_red_heart_and_collection_aliases_mean_my_liked_playlist(self):
        for text in ('播放我的红心歌单', '播放我的收藏歌单', '播放我喜欢的音乐',
                     '网易云播放我的红心歌单', '帮我播放我的网易云收藏歌单'):
            self.assertEqual(parse_control(text), ('play', dict(source='netease', playlist_kind='liked')))

    def test_named_and_numbered_playlists_are_not_song_queries(self):
        for text in ('播放我的网易云歌单《通勤》', '网易云播放我的歌单《通勤》',
                     '播放我收藏的歌单《通勤》', '播放歌单《通勤》'):
            self.assertEqual(parse_control(text), ('play', dict(source='netease', playlist_name='《通勤》')))
        self.assertEqual(parse_control('播放第二个歌单'), ('play', dict(source='netease', playlist_name='第二个歌单')))
        self.assertEqual(parse_control('播放歌单《收藏歌单》'), ('play', dict(source='netease', playlist_name='《收藏歌单》')))

    def test_playlist_negation_does_not_become_playback(self):
        for text in ('不要播放我的红心歌单', '如果播放我的收藏歌单', '他说“播放我的红心歌单”'):
            self.assertIsNone(parse_control(text))

    def test_next_is_operation_not_generated_title(self):
        for text in ('下一首。', '播放下一首', '请下一首吧'):
            self.assertEqual(parse_control(text), ('next', {}))

    def test_name_keeps_artist(self):
        self.assertEqual(parse_control('播放《测试歌曲-测试歌手》'), ('play', {'name': '《测试歌曲-测试歌手》'}))

    def test_natural_chat_song_lookup_and_play(self):
        self.assertEqual(parse_control('帮我查找歌名晴天'), ('search', dict(title='晴天')))
        self.assertEqual(parse_control('查找歌手周杰伦'), ('search', dict(artist='周杰伦')))
        self.assertEqual(parse_control('我想听周杰伦的晴天'), ('play', dict(name='周杰伦的晴天')))
        self.assertEqual(parse_control('帮我查找《晴天》然后播放'), ('play', dict(name='《晴天》')))
        self.assertEqual(parse_control('播放本地的晴天'), ('play', dict(name='晴天', source='local')))
        self.assertEqual(parse_control('查找本地歌曲晴天'), ('search', dict(title='晴天', source='local')))
        for text in ('帮我查找天气', '我想听一个故事', '不要播放晴天'):
            self.assertIsNone(parse_control(text, music_context=True))

    def test_repeat_mode_can_be_set_and_cancelled_through_chat(self):
        for text in ('单曲循环', '开启单曲循环', '把音乐模式设为单曲循环', '把这首歌改成单曲循环'):
            self.assertEqual(parse_control(text), ('mode', dict(mode='single')))
        self.assertEqual(parse_control('网易云单曲循环'), ('mode', dict(mode='single', source='netease')))
        self.assertEqual(parse_control('取消单曲循环'), ('mode', dict(mode='sequence')))
        self.assertIsNone(parse_control('不要单曲循环'))

    def test_chat_is_not_a_control(self):
        for text in ('今天天气怎样', '你喜欢下一首吗', '不要暂停音乐'):
            self.assertIsNone(parse_control(text))

    def test_music_context_accepts_pause_with_an_explanation(self):
        for text in ('暂停吧，下次再听我要去吃饭了', '先暂停一下，等会再听',
                     '请先暂停音乐吧，我要去吃饭了', '先不听了，下次再听'):
            self.assertEqual(parse_control(text, music_context=True), ('pause', {}))

    def test_natural_control_requires_music_context(self):
        self.assertIsNone(parse_control('暂停吧，下次再听我要去吃饭了'))

    def test_music_context_does_not_treat_negation_or_questions_as_controls(self):
        for text in ('不要暂停音乐', '先不要暂停音乐，我还要听', '你喜欢下一首吗',
                     '暂停播放是什么意思', '暂停提醒，音乐继续', '吃完饭再暂停音乐'):
            self.assertIsNone(parse_control(text, music_context=True))

    def test_pause_then_action_is_not_reduced_to_a_pause(self):
        from core.device.chassis_intent import classify_direct_chassis_request
        for text in ('暂停播放然后站起来', '先暂停音乐，然后站起来', '暂停播放吧，接着起立'):
            self.assertIsNone(parse_control(text, music_context=True))
            action, request = split_music_request(text)
            self.assertEqual(action, 'pause')
            self.assertEqual(classify_direct_chassis_request(request), 'self_chassis_stand_up')

    def test_split_does_not_turn_a_reason_or_negation_into_a_motion(self):
        from core.device.chassis_intent import classify_direct_chassis_request
        for text in ('暂停吧，下次再听我要去吃饭了', '不要暂停播放然后站起来',
                     '他说“暂停播放然后站起来”'):
            self.assertIsNone(split_music_request(text))
        self.assertIsNone(classify_direct_chassis_request(
            split_music_request('暂停播放然后不要站起来')[1]))


class MusicConsentTests(unittest.IsolatedAsyncioTestCase):
    async def test_shuffle_playlist_reply_and_command_use_same_mode(self):
        music = SimpleNamespace(state='playing', command=AsyncMock(return_value=dict(
            name='歌手 - 歌曲', playlistName='TUY鱼喜欢的音乐', mode='shuffle')))
        conn = SimpleNamespace(local_music=music, session_id='session', chat_lock=threading.Lock())
        async def pause_audio(conn, for_chat=True): music.state = 'paused'
        with patch('core.music.conversation.pause_for_chat', side_effect=pause_audio), \
                patch('core.handle.reportHandle.enqueue_asr_report', Mock()), \
                patch('core.music.conversation.say', new_callable=AsyncMock):
            handled, answer = await handle_input(conn, '随机播放我的红心歌单', web=True)
        self.assertTrue(handled)
        self.assertEqual(music.command.call_args.kwargs['mode'], 'shuffle')
        self.assertEqual(music.command.call_args.kwargs['playlist_kind'], 'liked')
        self.assertIn('随机放你的', answer)
        self.assertNotIn('第一首', answer)

    async def test_liked_playlist_reply_names_actual_account_playlist(self):
        music = SimpleNamespace(state='playing', command=AsyncMock(return_value=dict(
            name='歌手 - 歌曲', playlistName='TUY鱼喜欢的音乐')))
        conn = SimpleNamespace(local_music=music, session_id='session', chat_lock=threading.Lock())
        async def pause_audio(conn, for_chat=True): music.state = 'paused'
        with patch('core.music.conversation.pause_for_chat', side_effect=pause_audio), \
                patch('core.handle.reportHandle.enqueue_asr_report', Mock()), \
                patch('core.music.conversation.say', new_callable=AsyncMock):
            handled, answer = await handle_input(conn, '播放我的收藏歌单', web=True)
        self.assertTrue(handled)
        self.assertIn('TUY鱼喜欢的音乐', answer)
        kwargs = music.command.await_args.kwargs
        self.assertEqual(kwargs['playlist_kind'], 'liked')
        self.assertEqual(kwargs['source'], 'netease')
        self.assertNotIn('title', kwargs)
        self.assertFalse(conn.chat_lock.locked())

    async def test_posture_then_resume_routes_as_one_request_without_early_music(self):
        for web in (True, False):
            music = SimpleNamespace(state='playing', command=AsyncMock())
            conn = SimpleNamespace(local_music=music, session_id='session')
            async def pause_audio(conn, for_chat=True): music.state = 'paused'
            with patch('core.music.conversation.pause_for_chat', side_effect=pause_audio) as pause, \
                    patch('core.music.conversation.say', new_callable=AsyncMock) as say:
                handled, routed = await handle_input(conn, '站起来然后继续播放', web=web)
            self.assertFalse(handled)
            self.assertEqual(routed, '站起来然后继续播放')
            pause.assert_awaited_once_with(conn, for_chat=not web)
            music.command.assert_not_awaited()
            say.assert_not_awaited()

    async def test_chat_mode_change_resumes_music_after_acknowledgement(self):
        music = SimpleNamespace(state='playing', command=AsyncMock(return_value=dict(mode='single', name='晴天')))
        conn = SimpleNamespace(local_music=music, session_id='session', chat_lock=threading.Lock())
        async def pause(conn, for_chat=True): music.state = 'paused'
        with patch('core.music.conversation.pause_for_chat', side_effect=pause), \
                patch('core.handle.reportHandle.enqueue_asr_report', Mock()), \
                patch('core.music.conversation.say', new_callable=AsyncMock):
            handled, answer = await handle_input(conn, '单曲循环', web=True)
        self.assertTrue(handled)
        self.assertIn('单曲循环', answer)
        self.assertEqual([call.args[0] for call in music.command.await_args_list], ['mode','resume'])
        self.assertEqual(music.command.await_args_list[0].kwargs['after_sentence'], music.command.await_args_list[1].kwargs['after_sentence'])
        self.assertFalse(conn.chat_lock.locked())

    async def test_voice_chat_still_requires_confirmation(self):
        music = SimpleNamespace(state='playing', command=AsyncMock(), interrupted_for_chat=False)
        conn = SimpleNamespace(local_music=music, session_id='session')
        with patch('core.handle.reportHandle.enqueue_asr_report', Mock()), \
                patch('core.handle.reportHandle.enqueue_tts_report', Mock()), \
                patch('core.music.conversation.say', new_callable=AsyncMock):
            handled, answer = await handle_input(conn, '聊聊今天的事情')
            self.assertTrue(handled)
            self.assertIn('先停一下音乐', answer)
            music.command.assert_not_awaited()
            handled, replay = await handle_input(conn, '停止音乐并聊天')
            self.assertFalse(handled)
            self.assertEqual(replay, '聊聊今天的事情')
            music.command.assert_awaited_once_with('stop')

    async def test_web_chat_pauses_without_a_consent_question(self):
        music = SimpleNamespace(state='playing', command=AsyncMock(), position=42)
        conn = SimpleNamespace(local_music=music, session_id='session')
        async def pause_audio(conn, for_chat=True):
            conn.local_music.state = 'paused'
        with patch('core.music.conversation.pause_for_chat', side_effect=pause_audio) as pause, \
                patch('core.handle.reportHandle.enqueue_asr_report', Mock()) as asr, \
                patch('core.handle.reportHandle.enqueue_tts_report', Mock()) as tts:
            handled, routed = await handle_input(conn, '聊聊今天的事情', web=True)
        self.assertFalse(handled)
        self.assertEqual(routed, '聊聊今天的事情')
        pause.assert_awaited_once_with(conn, for_chat=False)
        self.assertEqual(music.state, 'paused')
        self.assertEqual(music.position, 42)
        music.command.assert_not_awaited()
        asr.assert_not_called()
        tts.assert_not_called()
        self.assertIsNone(conn.music_chat_pending)

    async def test_web_request_does_not_replay_pending_voice_question(self):
        import time
        conn = SimpleNamespace(local_music=None, session_id='session',
            music_chat_pending={'text': '旧问题', 'at': time.monotonic(), 'session': 'session'})
        with patch('core.music.conversation.pause_for_chat', new_callable=AsyncMock):
            handled, routed = await handle_input(conn, '站起来', web=True)
        self.assertFalse(handled)
        self.assertEqual(routed, '站起来')
        self.assertIsNone(conn.music_chat_pending)

    async def test_web_stop_then_request_stops_instead_of_pausing(self):
        music = SimpleNamespace(state='playing', command=AsyncMock())
        conn = SimpleNamespace(local_music=music, session_id='session')
        with patch('core.music.conversation.pause_for_chat', new_callable=AsyncMock) as pause:
            handled, routed = await handle_input(conn, '停止音乐然后站起来', web=True)
        self.assertFalse(handled)
        self.assertEqual(routed, '停止音乐然后站起来')
        music.command.assert_awaited_once_with('stop')
        pause.assert_not_awaited()

    async def test_explicit_keep_playing_does_not_pause_for_web_reply(self):
        music = SimpleNamespace(state='playing', command=AsyncMock())
        conn = SimpleNamespace(local_music=music, session_id='session', sentence_id='music-stream', headers={})
        with patch('core.music.conversation.pause_for_chat', new_callable=AsyncMock) as pause, \
                patch('core.handle.reportHandle.enqueue_asr_report', Mock()), \
                patch('core.handle.reportHandle.enqueue_tts_report', Mock()):
            handled, _ = await handle_input(conn, '不要暂停音乐', web=True)
        self.assertTrue(handled)
        pause.assert_not_awaited()
        music.command.assert_not_awaited()
