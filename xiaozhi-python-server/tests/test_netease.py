import asyncio
import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from core.music.netease import NeteaseClient, split_song_name, song_query, track_info
from core.music import MusicPlayer
from core.music.conversation import parse_control


def song(key, artist='周杰伦', title='晴天'):
    return dict(id=key, name=title, ar=[dict(name=artist)], al=dict(name='叶惠美'), dt=269000)


class AccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch('core.music.netease.ROOT', Path(self.temp.name)):
            self.client = NeteaseClient({})
        self.client.cookie = 'MUSIC_U=test-token'
        self.client.profile = dict(userId='10', nickname='测试用户', avatar='')
        self.client.ready = lambda: True
        self.client.call = AsyncMock()

    async def asyncTearDown(self): self.temp.cleanup()

    async def test_requires_one_meaningful_condition_and_filters_both_when_supplied(self):
        for artist,title in [('', ''), ('---', '晴天'), ('周杰伦', '...'), ('a'*101, '')]:
            with self.assertRaises(ValueError): await self.client.search(artist, title)
        self.client.call.assert_not_awaited()
        self.client.call.return_value = dict(code=200, result=dict(songCount=3,
            songs=[song(1), song(2, '其他歌手'), song(3, title='彩虹')]))
        page = await self.client.search('周杰伦', '晴天')
        self.assertEqual([row['id'] for row in page['items']], ['netease:1'])
        self.assertEqual(page['nextOffset'], 3)
        self.assertFalse(page['hasMore'])

    async def test_song_name_only_keeps_different_artists(self):
        self.client.call.return_value = dict(code=200, result=dict(songCount=3,
            songs=[song(1), song(2, '其他歌手'), song(3, title='彩虹')]))
        page = await self.client.search(title='晴天')
        self.assertEqual([row['id'] for row in page['items']], ['netease:1', 'netease:2'])
        self.client.call.assert_awaited_with('cloudsearch', keywords='晴天', type=1, limit=30, offset=0)

    async def test_artist_only_keeps_different_song_names(self):
        self.client.call.return_value = dict(code=200, result=dict(songCount=3,
            songs=[song(1), song(2, '其他歌手'), song(3, title='彩虹')]))
        page = await self.client.search(artist='周杰伦')
        self.assertEqual([row['id'] for row in page['items']], ['netease:1', 'netease:3'])
        self.client.call.assert_awaited_with('cloudsearch', keywords='周杰伦', type=1, limit=30, offset=0)

    async def test_playlist_filter_scans_unloaded_songs_and_preserves_cursor(self):
        self.client.playlist = AsyncMock(return_value=dict(name='大歌单', ids=[str(n) for n in range(1,202)]))
        self.client.songs = AsyncMock(side_effect=lambda ids: [track_info(song(int(key), title='彩虹' if key=='201' else '晴天')) for key in ids])
        first = await self.client.playlist_songs('90', query='彩虹')
        self.assertEqual(first['items'], [])
        self.assertEqual(first['nextOffset'], 200)
        self.assertTrue(first['hasMore'])
        second = await self.client.playlist_songs('90', offset=first['nextOffset'], query='彩虹')
        self.assertEqual([row['id'] for row in second['items']], ['netease:201'])
        self.assertFalse(second['hasMore'])

    async def test_download_uses_separate_download_endpoint_and_rejects_trials(self):
        self.client.call.return_value = dict(code=200, data=dict(id=1, url='https://m.music.126.net/song.mp3'))
        self.assertEqual(await self.client.download_url('netease:1'), 'https://m.music.126.net/song.mp3')
        self.client.call.assert_awaited_with('song_download_url_v1', id='1', level='exhigh')
        self.client.call.return_value = dict(code=200, data=dict(id=1, url='https://m.music.126.net/song.mp3', freeTrialInfo=dict(start=0, end=30)))
        with self.assertRaises(ValueError): await self.client.download_url('1')

    async def test_lyrics_are_cached_and_fit_proxy_response_buffer(self):
        self.client.call.return_value = dict(code=200, lrc=dict(lyric='歌'*30000), tlyric=dict(lyric='词'*30000))
        result = await self.client.lyrics('netease:1')
        self.assertLess(len(json.dumps(dict(code=0, data=result)).encode()), 256*1024)
        self.assertEqual(await self.client.lyrics('1'), result)
        self.client.call.assert_awaited_once()

    async def test_complete_playlist_ids_preserve_order_across_pages(self):
        self.client.call.side_effect = [dict(code=200, playlist=dict(id=90, name='我的歌单',
            tracks=[song(1)], trackIds=[dict(id=1), dict(id=2), dict(id=3)])),
            dict(code=200, songs=[song(3), song(2)])]
        page = await self.client.playlist_songs('90', offset=1, limit=2)
        self.assertEqual([row['id'] for row in page['items']], ['netease:2', 'netease:3'])
        self.assertEqual(page['total'], 3)
        self.assertFalse(page['hasMore'])

    async def test_trial_and_unsafe_urls_are_not_playable(self):
        for row in [dict(id=1, url=None), dict(id=1, url='https://m.music.126.net/song.mp3', freeTrialInfo=dict(start=0, end=30)),
            dict(id=1, url='file:///etc/passwd'), dict(id=1, url='https://music.126.net.evil.invalid/song.mp3'),
            dict(id=1, url='http://127.0.0.1/song.mp3')]:
            self.client.call.return_value = dict(code=200, data=[row])
            with self.assertRaises(ValueError): await self.client.play_url('netease:1')
        self.client.call.return_value = dict(code=200, data=[dict(id=1, url='https://m.music.126.net/song.mp3', freeTrialInfo=None)])
        self.assertEqual(await self.client.play_url('1'), 'https://m.music.126.net/song.mp3')
        self.client.call.assert_awaited_with('song_url_v1', id='1', level='exhigh', unblock='false')

    async def test_account_change_rejects_inflight_results(self):
        async def changed(*args, **kwargs):
            self.client.generation += 1
            return dict(code=200, songs=[song(1)])
        self.client.call.side_effect = changed
        with self.assertRaisesRegex(ValueError, '账号已变化'): await self.client.songs(['1'])
        self.assertEqual(self.client.tracks, {})

    async def test_qr_authorization_persists_credentials_without_returning_them(self):
        self.client.qr = dict(token='a'*32, key='private-key', expires=time.monotonic()+180)
        self.client.call.side_effect = [dict(code=803, cookie='MUSIC_U=secret; Path=/; __csrf=csrf; HttpOnly'),
            dict(data=dict(profile=dict(userId=10, nickname='测试用户', avatarUrl='')))]
        result = await self.client.check_qr('a'*32)
        self.assertEqual(result['state'], 'authorized')
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('cookie', result['account'])
        self.assertTrue(self.client.storage.exists())
        previous = self.client.generation
        self.client.forget()
        self.assertFalse(self.client.storage.exists())
        self.assertGreater(self.client.generation, previous)
        self.assertFalse((await self.client.account())['loggedIn'])

    async def test_expired_qr_does_not_call_platform(self):
        self.client.qr = dict(token='a'*32, key='key', expires=time.monotonic()-1)
        self.assertEqual(await self.client.check_qr('a'*32), dict(state='expired'))
        self.client.call.assert_not_awaited()

    async def test_liked_playlist_uses_owned_platform_marker_not_name_or_collection(self):
        self.client.profile['nickname'] = 'TUY鱼'
        self.client.call.return_value = dict(code=200, more=False, playlist=[
            dict(id=80, name='TUY鱼喜欢的音乐', creator=dict(userId=10), specialType=0),
            dict(id=81, name='别人的喜欢的音乐', creator=dict(userId=11), specialType=5),
            dict(id=82, name='自己的红心已改名', creator=dict(userId=10), specialType=5)])
        rows = await self.client.find_playlists(liked=True)
        self.assertEqual([row['id'] for row in rows], ['82'])
        self.assertTrue(rows[0]['liked'])

    async def test_liked_playlist_older_response_only_falls_back_to_exact_owned_name(self):
        self.client.profile['nickname'] = 'TUY鱼'
        self.client.call.return_value = dict(code=200, more=False, playlist=[
            dict(id=80, name='TUY鱼喜欢的音乐', creator=dict(userId=11)),
            dict(id=81, name='大家喜欢的音乐', creator=dict(userId=10)),
            dict(id=82, name='TUY鱼喜欢的音乐', userId=10)])
        rows = await self.client.find_playlists(liked=True)
        self.assertEqual([row['id'] for row in rows], ['82'])

    async def test_playlist_name_checks_later_pages_before_accepting_partial_match(self):
        self.client.call.side_effect = [dict(code=200, more=True, playlist=[
            dict(id=80, name='通勤备选', creator=dict(userId=10))]),
            dict(code=200, more=False, playlist=[dict(id=81, name='通勤', creator=dict(userId=10))])]
        rows = await self.client.find_playlists(name='《通勤》')
        self.assertEqual([row['id'] for row in rows], ['81'])
        self.client.call.assert_awaited_with('user_playlist', uid='10', offset=1, limit=100)

    async def test_duplicate_playlist_names_remain_candidates(self):
        self.client.call.return_value = dict(code=200, more=False, playlist=[
            dict(id=80, name='通勤', creator=dict(userId=10)),
            dict(id=81, name='通勤', creator=dict(userId=11))])
        self.assertEqual(len(await self.client.find_playlists(name='通勤')), 2)

    async def test_playlist_lookup_rejects_changed_account(self):
        async def changed(*args, **kwargs):
            self.client.generation += 1
            return dict(code=200, more=False, playlist=[])
        self.client.call.side_effect = changed
        with self.assertRaisesRegex(ValueError, '账号已变化'):
            await self.client.find_playlists(liked=True)


class TwoPlayerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        (Path(self.temp.name)/'本地歌曲.mp3').write_bytes(b'audio')
        self.conn = NS(config={'plugins':{'play_music':{'music_dir':self.temp.name}}}, logger=Mock())
        self.music = MusicPlayer(self.conn)
        self.client = Mock(generation=7)
        self.client.tracks = {f'netease:{key}':track_info(song(key)) for key in (1,2)}
        self.client.track = AsyncMock(side_effect=lambda value:dict(self.client.tracks[value]))
        self.client.playlist = AsyncMock(return_value=dict(ids=['1','2']))
        self.patch_client = patch('core.music.playback.netease', return_value=self.client)
        self.patch_ffmpeg = patch('core.music.playback.shutil.which', return_value='ffmpeg')
        self.patch_client.start(); self.patch_ffmpeg.start()
        async def run():
            self.music.state = 'playing'
            self.music.record_play()
            await asyncio.Event().wait()
        self.music.run = run

    async def asyncTearDown(self):
        await self.music.close()
        self.patch_client.stop(); self.patch_ffmpeg.stop(); self.temp.cleanup()

    async def test_switch_resume_keeps_independent_positions_volumes_and_modes(self):
        await self.music.command('play', source='local')
        await asyncio.sleep(0)
        self.music.position = 12.5
        await self.music.command('volume', source='local', volume=35)
        await self.music.command('play', source='netease', track_id='netease:1', playlist_id='90')
        await asyncio.sleep(0)
        self.music.position = 22
        await self.music.command('volume', source='netease', volume=80)
        await self.music.command('mode', source='netease', mode='shuffle')
        status = await self.music.command('resume', source='local')
        self.assertEqual((status['position'], status['volume'], status['mode']), (12.5,35,'sequence'))
        self.assertEqual(status['players']['netease']['position'], 22)
        self.assertEqual(status['players']['netease']['state'], 'paused')
        status = await self.music.command('resume', source='netease')
        self.assertEqual((status['position'], status['volume'], status['mode']), (22,80,'shuffle'))
        self.assertEqual(self.music.online_queue, ['netease:1','netease:2'])

    async def test_inactive_volume_or_stop_does_not_interrupt_other_player(self):
        await self.music.command('play', source='local')
        await asyncio.sleep(0)
        task = self.music.task
        await self.music.command('volume', source='netease', volume=15)
        await self.music.command('stop', source='netease')
        self.assertIs(self.music.task, task)
        self.assertEqual(self.music.state, 'playing')
        self.assertEqual(self.music.status()['players']['netease']['volume'], 15)

    async def test_bad_selection_does_not_stop_current_player(self):
        await self.music.command('play', source='local')
        await asyncio.sleep(0)
        task = self.music.task
        self.client.track.side_effect = ValueError('歌曲不存在')
        with self.assertRaises(ValueError): await self.music.command('play', source='netease', track_id='netease:99')
        self.assertIs(self.music.task, task)

    async def test_liked_playlist_queues_all_songs_and_keeps_next_inside_playlist(self):
        self.client.find_playlists = AsyncMock(return_value=[dict(id='90', name='TUY鱼喜欢的音乐', created=True, liked=True)])
        self.client.playlist.return_value = dict(id='90', name='TUY鱼喜欢的音乐', ids=['1', '2'])
        self.client.search = AsyncMock()
        status = await self.music.command('play', playlist_kind='liked', after_sentence='ack')
        self.assertEqual((status['source'], status['trackId'], status['playlistName']), ('netease', 'netease:1', 'TUY鱼喜欢的音乐'))
        self.assertEqual(self.music.online_queue, ['netease:1', 'netease:2'])
        self.assertEqual(self.music.awaiting_sentence, 'ack')
        self.client.find_playlists.assert_awaited_once_with(name=None, liked=True)
        self.client.search.assert_not_awaited()
        status = await self.music.command('next')
        self.assertEqual(status['trackId'], 'netease:2')
        self.assertEqual(status['playlistName'], 'TUY鱼喜欢的音乐')

    async def test_failed_shuffle_next_does_not_consume_the_queued_song(self):
        await self.music.command('play', source='netease', track_id='netease:1', playlist_id='90')
        await asyncio.sleep(0)
        await self.music.command('mode', source='netease', mode='shuffle')
        self.music.shuffle_queue = ['netease:2']
        before = self.music.task
        self.client.track.side_effect = ValueError('temporary metadata failure')
        with self.assertRaises(ValueError): await self.music.command('next', source='netease')
        self.assertEqual(['netease:2'], self.music.shuffle_queue)
        self.assertIs(before, self.music.task); self.assertFalse(before.done())
        self.client.track.side_effect = lambda value:dict(self.client.tracks[value])
        result = await self.music.command('next', source='netease')
        self.assertEqual('netease:2', result['trackId'])

    async def test_failed_previous_does_not_change_pending_history_cursor(self):
        await self.music.command('play', source='netease', track_id='netease:1', playlist_id='90')
        await asyncio.sleep(0)
        await self.music.command('next', source='netease')
        await asyncio.sleep(0)
        self.assertEqual(['netease:1', 'netease:2'], self.music.history)
        self.client.track.side_effect = ValueError('temporary metadata failure')
        with self.assertRaises(ValueError): await self.music.command('previous', source='netease')
        self.assertIsNone(self.music.pending_history_index)
        self.assertEqual(1, self.music.history_index)
        self.assertEqual('netease:2', self.music.track['id'])

    async def test_named_playlist_matches_account_playlist_and_never_song_search(self):
        self.client.find_playlists = AsyncMock(return_value=[dict(id='90', name='通勤', created=False)])
        self.client.playlist.return_value = dict(name='通勤', ids=['2', '1'])
        self.client.search = AsyncMock()
        status = await self.music.command('play', playlist_name='《通勤》')
        self.assertEqual(status['trackId'], 'netease:2')
        self.assertEqual(self.music.online_queue, ['netease:2', 'netease:1'])
        self.client.find_playlists.assert_awaited_once_with(name='《通勤》', liked=False)
        self.client.search.assert_not_awaited()

    async def test_shuffle_playlist_randomizes_first_song_and_does_not_repeat_next(self):
        self.client.find_playlists = AsyncMock(return_value=[dict(id='90', name='红心', created=True)])
        self.client.playlist.return_value = dict(name='红心', ids=['1', '2'])
        with patch('core.music.playback.random.choice', return_value='2') as choice:
            status = await self.music.command('play', playlist_kind='liked', mode='shuffle')
        choice.assert_called_once_with(['1', '2'])
        self.assertEqual((status['mode'], status['trackId']), ('shuffle', 'netease:2'))
        self.assertEqual(self.music.online_queue, ['netease:1', 'netease:2'])
        self.assertEqual(status['players']['local']['mode'], 'sequence')
        await asyncio.sleep(0)
        self.assertEqual((await self.music.command('next'))['trackId'], 'netease:1')
        await asyncio.sleep(0)
        self.assertEqual((await self.music.command('previous'))['trackId'], 'netease:2')

    async def test_sequence_playlist_overrides_previous_shuffle_and_starts_first(self):
        await self.music.command('mode', source='netease', mode='shuffle')
        self.client.playlist.return_value = dict(name='红心', ids=['1', '2'])
        with patch('core.music.playback.random.choice') as choice:
            status = await self.music.command('play', source='netease', playlist_id='90', mode='sequence')
        self.assertEqual((status['mode'], status['trackId']), ('sequence', 'netease:1'))
        choice.assert_not_called()

    async def test_playlist_candidate_selection_keeps_requested_shuffle(self):
        self.client.find_playlists = AsyncMock(return_value=[dict(id='90', name='通勤', created=True), dict(id='91', name='通勤', created=False)])
        with self.assertRaisesRegex(ValueError, '第几个歌单'):
            await self.music.command('play', playlist_name='通勤', mode='shuffle')
        self.client.playlist.return_value = dict(name='通勤', ids=['1', '2'])
        with patch('core.music.playback.random.choice', return_value='2'):
            status = await self.music.command('play', playlist_name='第二个歌单')
        self.assertEqual((status['mode'], status['trackId']), ('shuffle', 'netease:2'))
        self.client.playlist.assert_awaited_with('91')

    async def start_blocked_selection(self):
        entered = asyncio.Event()
        async def stuck(value):
            entered.set()
            await asyncio.Event().wait()
        self.client.track.side_effect = stuck
        operation = asyncio.create_task(self.music.command('play', source='netease', track_id='netease:1'))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertTrue(self.music.lock.locked())
        return operation

    async def assert_selection_cancelled(self, operation):
        with self.assertRaisesRegex(ValueError, '新的操作取消'):
            await asyncio.wait_for(operation, 1)
        self.assertIsNone(self.music.preparing)
        self.assertFalse(self.music.lock.locked())

    async def test_stop_cancels_network_selection_before_waiting_for_lock(self):
        operation = await self.start_blocked_selection()
        status = await asyncio.wait_for(self.music.command('stop', source='netease'), 1)
        await self.assert_selection_cancelled(operation)
        self.assertEqual(status['players']['netease']['state'], 'stopped')
        self.assertIsNone(self.music.track)
        self.assertIsNone(self.music.task)

    async def test_pause_cancels_liked_playlist_lookup_before_player_lock(self):
        entered = asyncio.Event()
        async def stuck(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        self.client.find_playlists = AsyncMock(side_effect=stuck)
        operation = asyncio.create_task(self.music.command('play', playlist_kind='liked'))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertFalse(self.music.lock.locked())
        await asyncio.wait_for(self.music.command('pause', source='netease'), 1)
        await self.assert_selection_cancelled(operation)
        self.assertIsNone(self.music.task)

    async def test_song_metadata_timeout_releases_lock_without_late_playback(self):
        cancelled = []
        async def stuck(value):
            try: await asyncio.Event().wait()
            finally: cancelled.append(value)
        self.client.track.side_effect = stuck
        wait_for = asyncio.wait_for
        with patch('core.music.playback.asyncio.wait_for', side_effect=lambda operation, timeout: wait_for(operation, timeout=.01)):
            with self.assertRaisesRegex(ValueError, '音乐操作超时'):
                await self.music.command('play', source='netease', track_id='netease:1')
        self.assertEqual(cancelled, ['netease:1'])
        self.assertFalse(self.music.lock.locked())
        self.assertIsNone(self.music.preparing)
        self.assertIsNone(self.music.task)

    async def test_new_local_play_supersedes_blocked_online_selection(self):
        operation = await self.start_blocked_selection()
        status = await asyncio.wait_for(self.music.command('play', source='local'), 1)
        await self.assert_selection_cancelled(operation)
        self.assertEqual(status['source'], 'local')
        self.assertNotEqual(status['trackId'], 'netease:1')

    async def test_chat_cancels_selection_even_before_playback_state_changes(self):
        from core.music import pause_for_chat
        self.conn.local_music = self.music
        operation = await self.start_blocked_selection()
        self.assertEqual(self.music.state, 'stopped')
        await asyncio.wait_for(pause_for_chat(self.conn), 1)
        await self.assert_selection_cancelled(operation)
        self.assertIsNone(self.music.task)

    async def test_inactive_source_stop_does_not_cancel_another_source_selection(self):
        operation = await self.start_blocked_selection()
        stop = asyncio.create_task(self.music.command('stop', source='local'))
        await asyncio.sleep(0)
        self.assertIsNotNone(self.music.preparing)
        self.assertFalse(operation.done())
        await self.music.cancel_preparation()
        await self.assert_selection_cancelled(operation)
        await asyncio.wait_for(stop, 1)

    async def test_close_cancels_network_selection_without_waiting_for_timeout(self):
        operation = await self.start_blocked_selection()
        await asyncio.wait_for(self.music.close(), 1)
        await self.assert_selection_cancelled(operation)
        self.assertTrue(self.music.closing)

    async def test_stop_invalidates_replacement_while_previous_request_is_cleaning_up(self):
        entered = asyncio.Event(); cleaning = asyncio.Event(); released = asyncio.Event()
        async def slow_cleanup(value):
            entered.set()
            try: await asyncio.Event().wait()
            finally:
                cleaning.set()
                await released.wait()
        self.client.track.side_effect = slow_cleanup
        first = asyncio.create_task(self.music.command('play', source='netease', track_id='netease:1'))
        await asyncio.wait_for(entered.wait(), 1)
        replacement = asyncio.create_task(self.music.command('play', source='local'))
        await asyncio.wait_for(cleaning.wait(), 1)
        stop = asyncio.create_task(self.music.command('stop', source='local'))
        await asyncio.sleep(0)
        released.set()
        for operation in (first, replacement):
            with self.assertRaisesRegex(ValueError, '新的操作取消'):
                await asyncio.wait_for(operation, 1)
        status = await asyncio.wait_for(stop, 1)
        self.assertEqual(status['state'], 'stopped')
        self.assertIsNone(self.music.task)
        self.assertIsNone(self.music.preparing)

    async def test_online_stop_cancels_automatic_local_search_fallback(self):
        self.client.search = AsyncMock(return_value=dict(items=[track_info(song(1))], hasMore=False))
        entered = asyncio.Event()
        async def stuck(value):
            entered.set()
            await asyncio.Event().wait()
        self.client.track.side_effect = stuck
        operation = asyncio.create_task(self.music.command('play', artist='周杰伦', title='晴天'))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(self.music.command('stop', source='netease'), 1)
        await self.assert_selection_cancelled(operation)
        self.assertIsNone(self.music.task)

    async def test_invalid_playlist_mode_preserves_active_local_song(self):
        await self.music.command('play', source='local')
        task = self.music.task
        with self.assertRaisesRegex(ValueError, '播放'):
            await self.music.command('play', source='netease', playlist_id='90', mode='bad')
        self.assertIs(self.music.task, task)

    async def test_playlist_aliases_resolve_red_heart_playlist_in_tool_calls(self):
        self.client.find_playlists = AsyncMock(return_value=[dict(id='90', name='TUY鱼喜欢的音乐', created=True)])
        self.client.playlist.return_value = dict(name='TUY鱼喜欢的音乐', ids=['1', '2'])
        for name in ('我的红心歌单', '我的收藏歌单', '我喜欢的音乐'):
            await self.music.command('play', playlist_name=name)
            self.client.find_playlists.assert_awaited_with(name=name, liked=True)

    async def test_empty_playlist_does_not_replace_current_music(self):
        await self.music.command('play', source='local')
        await asyncio.sleep(0)
        task = self.music.task
        self.client.find_playlists = AsyncMock(return_value=[dict(id='90', name='空歌单', created=True)])
        self.client.playlist.return_value = dict(name='空歌单', ids=[])
        with self.assertRaisesRegex(ValueError, '没有歌曲'):
            await self.music.command('play', playlist_name='空歌单')
        self.assertIs(self.music.task, task)
        self.assertEqual(self.music.source, 'local')

    async def test_playlist_candidates_do_not_overlap_song_candidates_and_expire(self):
        self.conn.session_id = 'session'
        self.conn.music_candidates = dict(items=[self.client.tracks['netease:1']], at=time.monotonic(), source='netease', session='session', generation=7)
        self.client.find_playlists = AsyncMock(return_value=[dict(id='90', name='通勤', created=True), dict(id='91', name='通勤', created=False)])
        with self.assertRaisesRegex(ValueError, '第几个歌单'):
            await self.music.command('play', playlist_name='通勤')
        self.assertEqual(self.conn.music_candidates['items'][0]['id'], 'netease:1')
        self.client.playlist.return_value = dict(name='通勤', ids=['2', '1'])
        status = await self.music.command('play', playlist_name='第二个歌单')
        self.client.playlist.assert_awaited_with('91')
        self.assertEqual(status['trackId'], 'netease:2')
        self.conn.music_playlist_candidates['at'] -= 121
        with self.assertRaisesRegex(ValueError, '候选已过期'):
            await self.music.command('play', playlist_name='第一个歌单')

    async def test_playlist_selection_rejects_a_session_change_during_lookup(self):
        self.conn.session_id = 'old'
        async def changed(**kwargs):
            self.conn.session_id = 'new'
            return [dict(id='90', name='通勤', created=True)]
        self.client.find_playlists = AsyncMock(side_effect=changed)
        with self.assertRaisesRegex(ValueError, '会话已变化'):
            await self.music.command('play', playlist_name='通勤')
        self.assertIsNone(self.music.track)

    async def test_playlist_lookup_timeout_does_not_queue_late_playback(self):
        cancelled = []
        async def stuck(**kwargs):
            try: await asyncio.Event().wait()
            finally: cancelled.append(True)
        self.client.find_playlists = AsyncMock(side_effect=stuck)
        wait_for = asyncio.wait_for
        with patch('core.music.playback.asyncio.wait_for', side_effect=lambda operation, timeout: wait_for(operation, timeout=.01)):
            with self.assertRaisesRegex(ValueError, '歌单超时'):
                await self.music.command('play', playlist_kind='liked')
        self.assertEqual(cancelled, [True])
        self.assertIsNone(self.music.task)
        self.assertIsNone(self.music.track)
        self.assertEqual(self.music.source, 'local')

    async def test_remote_next_uses_playlist_order_and_previous_uses_history(self):
        await self.music.command('play', source='netease', track_id='netease:1', playlist_id='90')
        await asyncio.sleep(0)
        status = await self.music.command('next')
        self.assertEqual(status['trackId'], 'netease:2')
        await asyncio.sleep(0)
        status = await self.music.command('previous')
        self.assertEqual(status['trackId'], 'netease:1')

    async def test_uninitialized_resume_does_not_stop_other_player(self):
        await self.music.command('play', source='local')
        await asyncio.sleep(0)
        task = self.music.task
        with self.assertRaises(ValueError): await self.music.command('resume', source='netease')
        self.assertIs(self.music.task, task)

    async def test_voice_candidate_selection_is_bound_to_account_and_session(self):
        self.conn.session_id = 'session'
        self.client.tracks['netease:2'] = track_info(song(2, title='晴天 (Live)'))
        self.client.search = AsyncMock(return_value=dict(items=list(self.client.tracks.values()), hasMore=False))
        await self.music.command('search', artist='周杰伦', title='晴天')
        status = await self.music.command('play', name='第二首')
        self.assertEqual(status['trackId'], 'netease:2')
        self.conn.session_id = 'new-session'
        with self.assertRaisesRegex(ValueError, '候选已过期'): await self.music.command('play', name='第一首')

    async def test_seek_restarts_playback_without_changing_queue_volume_or_mode(self):
        await self.music.command('play', source='netease', track_id='netease:1', playlist_id='90')
        await asyncio.sleep(0)
        await self.music.command('volume', source='netease', volume=45)
        await self.music.command('mode', source='netease', mode='shuffle')
        previous = self.music.task
        status = await self.music.command('seek', source='netease', track_id='netease:1', position=100)
        self.assertTrue(previous.done())
        self.assertEqual((status['position'], status['volume'], status['mode']), (100,45,'shuffle'))
        self.assertEqual(self.music.online_queue, ['netease:1','netease:2'])
        await asyncio.sleep(0)
        self.assertEqual(self.music.state, 'playing')

    async def test_seeking_inactive_player_does_not_interrupt_active_player(self):
        await self.music.command('play', source='netease', track_id='netease:1')
        await asyncio.sleep(0)
        await self.music.command('play', source='local')
        await asyncio.sleep(0)
        task = self.music.task
        status = await self.music.command('seek', source='netease', track_id='netease:1', position=80)
        self.assertIs(self.music.task, task)
        self.assertEqual(self.music.state, 'playing')
        self.assertEqual(status['players']['netease']['position'], 80)
        self.assertEqual(status['players']['netease']['state'], 'paused')

    async def test_seek_rejects_stale_track_and_invalid_positions(self):
        await self.music.command('play', source='netease', track_id='netease:1')
        await asyncio.sleep(0)
        task = self.music.task
        with self.assertRaisesRegex(ValueError, '歌曲已变化'):
            await self.music.command('seek', source='netease', track_id='netease:2', position=10)
        for value in (True, -1, float('nan'), float('inf'), 300):
            with self.assertRaises(ValueError):
                await self.music.command('seek', source='netease', track_id='netease:1', position=value)
        self.assertIs(self.music.task, task)

    async def test_paused_seek_keeps_player_paused(self):
        await self.music.command('play', source='netease', track_id='netease:1')
        await asyncio.sleep(0)
        await self.music.command('pause')
        status = await self.music.command('seek', source='netease', track_id='netease:1', position=269)
        self.assertEqual(status['state'], 'paused')
        self.assertAlmostEqual(status['position'], 268.85)
        self.assertIsNone(self.music.task)

    async def test_chat_prefers_local_song_even_while_online_music_is_active(self):
        (Path(self.temp.name)/'周杰伦-晴天.mp3').write_bytes(b'local')
        await self.music.command('play', source='netease', track_id='netease:2')
        await asyncio.sleep(0)
        self.client.search = AsyncMock()
        status = await self.music.command('play', artist='周杰伦', title='晴天')
        self.assertEqual(status['source'], 'local')
        self.assertEqual(status['title'], '晴天')
        self.client.search.assert_not_awaited()

    async def test_chat_search_prefers_local_and_choice_works_without_online_account(self):
        self.conn.session_id = 'session'
        for artist in ('周杰伦', '其他歌手'):
            (Path(self.temp.name)/(artist+'-晴天.mp3')).write_bytes(b'local')
        self.client.search = AsyncMock(side_effect=ValueError('尚未登录'))
        result = await self.music.command('search', title='晴天')
        self.assertEqual(result['source'], 'local')
        self.assertEqual(len(result['results']), 2)
        self.assertTrue(all('_path' not in row for row in result['results']))
        status = await self.music.command('play', name='第二首')
        self.assertEqual(status['source'], 'local')
        self.assertEqual(status['trackId'], result['results'][1]['id'])
        self.client.search.assert_not_awaited()

    async def test_missing_local_song_searches_online_and_plays_duplicate_studio_recording(self):
        self.client.search = AsyncMock(return_value=dict(items=list(self.client.tracks.values()), hasMore=False))
        status = await self.music.command('play', name='周杰伦的晴天')
        self.client.search.assert_awaited_once_with('周杰伦', '晴天')
        self.assertEqual(status['trackId'], 'netease:1')

    async def test_song_and_artist_plays_studio_over_live_and_compilations(self):
        original = dict(self.client.tracks['netease:1'], album='原始专辑', albumType='专辑')
        compilation = dict(self.client.tracks['netease:2'], album='精选全纪录')
        live = dict(original, id='netease:3', title='晴天 (Live)', album='现场音乐会')
        self.client.tracks.update({row['id']:row for row in [original, compilation, live]})
        self.client.search = AsyncMock(return_value=dict(items=[compilation, live, original], hasMore=True))
        status = await self.music.command('play', artist='周杰伦', title='晴天')
        self.assertEqual(status['trackId'], 'netease:1')

    async def test_unrequested_live_only_recording_requires_selection(self):
        live = dict(self.client.tracks['netease:1'], title='晴天 (Live)', album='现场音乐会')
        self.client.tracks[live['id']] = live
        self.client.search = AsyncMock(return_value=dict(items=[live], hasMore=False))
        with self.assertRaisesRegex(ValueError, '请说播放第几首'):
            await self.music.command('play', title='晴天')
        status = await self.music.command('play', name='第一首')
        self.assertEqual(status['trackId'], 'netease:1')

    async def test_online_candidates_omit_albums_but_still_allow_numbered_selection(self):
        rows = [dict(self.client.tracks['netease:1'], album='原始专辑'),
                dict(self.client.tracks['netease:2'], artist='其他歌手', name='其他歌手 - 晴天', album='另一专辑')]
        self.client.search = AsyncMock(return_value=dict(items=rows, hasMore=False))
        with self.assertRaises(ValueError) as error:
            await self.music.command('play', title='晴天')
        self.assertIn('周杰伦 - 晴天', str(error.exception))
        self.assertNotIn('原始专辑', str(error.exception))
        self.assertNotIn('另一专辑', str(error.exception))
        status = await self.music.command('play', name='第二首')
        self.assertEqual(status['trackId'], 'netease:2')

    async def test_unrequested_local_live_only_recording_requires_selection(self):
        path = Path(self.temp.name)/'周杰伦-晴天 (Live).mp3'
        path.write_bytes(b'live-audio')
        with self.assertRaisesRegex(ValueError, '播放第几首'):
            await self.music.command('play', artist='周杰伦', title='晴天')
        status = await self.music.command('play', name='第一首')
        self.assertEqual(status['source'], 'local')
        self.assertEqual(status['title'], '晴天 (Live)')

    async def test_explicit_online_request_overrides_local_matching(self):
        (Path(self.temp.name)/'周杰伦-晴天.mp3').write_bytes(b'local')
        self.client.search = AsyncMock(return_value=dict(items=[self.client.tracks['netease:1']], hasMore=False))
        status = await self.music.command('play', source='netease', title='晴天')
        self.assertEqual(status['source'], 'netease')
        self.client.search.assert_awaited_once_with(None, '晴天')

    async def test_partial_artist_and_song_query_still_checks_local_first(self):
        (Path(self.temp.name)/'周杰伦-晴天.mp3').write_bytes(b'local')
        self.client.search = AsyncMock()
        status = await self.music.command('play', artist='周杰伦', title='晴')
        self.assertEqual((status['source'], status['title']), ('local', '晴天'))
        self.client.search.assert_not_awaited()

    async def test_word_de_in_a_song_title_does_not_require_a_matching_artist(self):
        self.client.tracks['netease:3'] = track_info(song(3, artist='张杰', title='最美的太阳'))
        self.client.search = AsyncMock(side_effect=[dict(items=[], hasMore=False), dict(items=[self.client.tracks['netease:3']], hasMore=False)])
        status = await self.music.command('play', name='最美的太阳')
        self.assertEqual(status['trackId'], 'netease:3')
        self.client.search.assert_awaited_with(title='最美的太阳')

    async def test_single_mode_is_independent_and_manual_next_still_changes_song(self):
        await self.music.command('play', source='netease', track_id='netease:1', playlist_id='90')
        await asyncio.sleep(0)
        task = self.music.task
        await self.music.command('mode', source='netease', mode='single')
        self.assertIs(self.music.task, task)
        status = await self.music.command('mode', source='local', mode='shuffle')
        self.assertEqual(status['mode'], 'single')
        self.assertEqual(status['players']['local']['mode'], 'shuffle')
        status = await self.music.command('next')
        self.assertEqual((status['trackId'], status['mode']), ('netease:2', 'single'))
        await asyncio.sleep(0)
        status = await self.music.command('previous')
        self.assertEqual(status['trackId'], 'netease:1')

    async def repeat_twice(self, source, lyric_data=None, lyric_provider=None, start_position=0):
        await self.music.command('play', source=source, track_id='netease:1' if source=='netease' else None, playlist_id='90' if source=='netease' else None)
        await asyncio.sleep(0)
        await self.music.halt()
        await self.music.command('mode', mode='single')
        self.music.position = start_position
        self.music.playback_stop = asyncio.Event()  # A fresh run after the fixture's halt.
        self.conn.stop_event = threading.Event()
        self.conn.chat_lock = threading.Lock()
        self.conn.client_is_speaking = False
        self.conn.tts = NS(tts_text_queue=queue.Queue(), tts_audio_queue=queue.Queue())
        self.conn.sample_rate = 24000
        self.conn.features = dict(standby_connection=True)
        event = asyncio.Event(); event.set()
        self.conn.audio_rate_controller = NS(queue_empty_event=event, reset=Mock())
        played = []
        async def completed(conn):
            played.append(self.music.track['id'])
            if len(played)==2: conn.stop_event.set()
        def decoder(*args, **kwargs):
            return NS(stdout=NS(readexactly=AsyncMock(side_effect=[b'\0'*2880]*4+[asyncio.IncompleteReadError(b'',2880)])),
                returncode=0, wait=AsyncMock(return_value=0), communicate=AsyncMock(return_value=(b'',b'')))
        self.client.play_url = AsyncMock(return_value='https://m.music.126.net/test.mp3')
        self.client.lyrics = lyric_provider or AsyncMock(return_value=lyric_data or {})
        with patch('core.music.playback.asyncio.create_subprocess_exec', new=AsyncMock(side_effect=decoder)), \
                patch('core.music.playback.local_details', new=AsyncMock(return_value=dict(durationMs=60000, **(lyric_data or {})))), \
                patch.dict('sys.modules', {'opuslib_next':NS(Encoder=lambda *args:NS(encode=lambda *args:b'opus'), APPLICATION_AUDIO=1)}), \
                patch('core.handle.sendAudioHandle.sendAudio', new=AsyncMock()), \
                patch('core.handle.sendAudioHandle.send_tts_message', new=AsyncMock()) as captions, \
                patch('core.handle.sendAudioHandle._wait_for_audio_completion', new=AsyncMock(side_effect=completed)), \
                patch('core.conversation.standby.begin_interaction'), \
                patch('core.conversation.standby.enter_standby', new=AsyncMock()):
            await asyncio.wait_for(MusicPlayer.run(self.music), 1)
        self.assertEqual(len(played), 2)
        self.assertEqual(played[0], played[1])
        self.assertEqual(self.music.position, 0)
        self.assertFalse(self.conn.chat_lock.locked())
        if source=='netease': self.assertEqual(self.client.play_url.await_count, 2)
        return [call for call in captions.await_args_list if call.args[1]=='sentence_start']

    async def test_local_single_mode_repeats_after_real_end_of_stream(self):
        await self.repeat_twice('local')

    async def test_online_single_mode_refreshes_audio_url_each_repeat(self):
        await self.repeat_twice('netease')

    async def test_local_lcd_lyrics_change_once_per_line_and_restart_on_repeat(self):
        calls = await self.repeat_twice('local', dict(lyric='[00:00]第一句\n[00:00.06]\n[00:00.12]第二句'))
        self.assertEqual([call.args[2] for call in calls], ['第一句', '第二句']*2)
        self.assertTrue(all(call.kwargs['media']=='music' for call in calls))
        self.assertEqual(len({call.kwargs['stream_id'] for call in calls}), 2)

    async def test_local_lcd_seek_starts_at_current_line(self):
        calls = await self.repeat_twice('local', dict(lyric='[00:00]第一句\n[00:00.12]第二句'), start_position=.13)
        self.assertEqual(calls[0].args[2], '第二句')

    async def test_online_lcd_lyrics_follow_playback_and_rebind_on_repeat(self):
        calls = await self.repeat_twice('netease', dict(lyric='[00:00]第一句\n[00:00.06]\n[00:00.12]第二句'))
        lyrics = [call for call in calls if call.args[2] in ('第一句', '第二句')]
        self.assertEqual([call.args[2] for call in lyrics], ['第一句', '第二句']*2)
        self.assertEqual(len({call.kwargs['stream_id'] for call in lyrics}), 2)
        # The initial song caption is allowed; it must never return between lyrics.
        for stream_id in {call.kwargs['stream_id'] for call in calls}:
            stream = [call.args[2] for call in calls if call.kwargs['stream_id']==stream_id]
            first = stream.index('第一句')
            self.assertEqual(stream[first:], ['第一句', '第二句'])

    async def test_slow_lyrics_do_not_hold_audio_and_are_cancelled_between_songs(self):
        cancelled = []
        async def slow_lyrics(value):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(value)
        calls = await self.repeat_twice('netease', lyric_provider=AsyncMock(side_effect=slow_lyrics))
        self.assertEqual(cancelled, ['netease:1']*2)
        self.assertTrue(all(call.args[2].startswith('正在播放：') for call in calls))

    async def test_lyric_failure_keeps_audio_playing_with_song_caption(self):
        calls = await self.repeat_twice('netease', lyric_provider=AsyncMock(side_effect=ValueError('歌词超时')))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call.args[2].startswith('正在播放：') for call in calls))


class OnlineVoiceTests(unittest.TestCase):
    def test_explicit_platform_and_search_accept_separate_conditions(self):
        self.assertEqual(parse_control('用网易云播放周杰伦的晴天'), ('play', dict(name='周杰伦的晴天', source='netease')))
        self.assertEqual(parse_control('网易云搜索周杰伦的晴天'), ('search', dict(artist='周杰伦', title='晴天', source='netease')))
        self.assertEqual(parse_control('网易云搜索歌手周杰伦'), ('search', dict(artist='周杰伦', source='netease')))
        self.assertEqual(parse_control('网易云搜索歌名晴天'), ('search', dict(title='晴天', source='netease')))
        self.assertIsNone(split_song_name('周杰伦的歌'))

    def test_song_request_preserves_quoted_title_and_artist_only_conditions(self):
        self.assertEqual(song_query('《最美的太阳》'), dict(title='最美的太阳'))
        self.assertEqual(song_query('歌名最美的太阳'), dict(title='最美的太阳'))
        self.assertEqual(song_query('周杰伦的歌'), dict(artist='周杰伦'))


class OnlineSearchReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_reply_does_not_require_player_status_fields(self):
        from core.music.conversation import handle_input
        conn = NS(session_id='session', chat_lock=threading.Lock(), local_music=None)
        music = NS(command=AsyncMock(return_value=dict(results=[track_info(song(1))], hasMore=False)))
        with patch('core.music.conversation.player', return_value=music), \
                patch('core.music.conversation.pause_for_chat', new_callable=AsyncMock), \
                patch('core.music.conversation.say', new_callable=AsyncMock), \
                patch('core.handle.reportHandle.enqueue_asr_report', Mock()), \
                patch('core.handle.reportHandle.enqueue_tts_report', Mock()):
            handled, answer = await handle_input(conn, '网易云搜索周杰伦的晴天', web=True)
        self.assertTrue(handled)
        self.assertIn('周杰伦的晴天', answer)
        self.assertNotIn('叶惠美', answer)


if __name__ == '__main__': unittest.main()
