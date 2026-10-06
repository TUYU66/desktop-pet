import asyncio
import json
import queue
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from core.music import MusicLibrary, MusicPlayer, validate_mp3, pause_for_chat
from core.api.music_handler import MusicHandler


class LibraryTests(unittest.TestCase):
    def test_artist_first_filename_supports_song_and_artist_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('周杰伦-彩虹.mp3', '周杰伦-晴天.mp3', 'Jux - can\'t say(不说).mp3'):
                (root/name).write_bytes(b'test')
            library = MusicLibrary({'plugins': {'play_music': {'music_dir': folder}}})
            track = library.select(name='彩虹')
            self.assertEqual((track['artist'], track['title']), ('周杰伦', '彩虹'))
            self.assertEqual(library.select(name='Jux的can\'t say(不说)')['title'], 'can\'t say(不说)')
            playlist = library.select(name='周杰伦的歌')
            self.assertEqual(playlist['_playlist_artist'], '周杰伦')
            self.assertEqual(len([item for item in library.tracks() if item['artist'] == '周杰伦']), 2)

    def test_names_ids_case_and_missing_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('晴天.mp3', '晴天现场.MP3', '忽略.wav'):
                (root/name).write_bytes(b'test')
            library = MusicLibrary({'plugins':{'play_music':{'music_dir':folder}}})
            self.assertEqual(len(library.tracks()), 2)
            chosen = library.select(name='晴天')
            self.assertEqual(library.select(track_id=chosen['id'])['name'], '晴天')
            with self.assertRaises(ValueError): library.select(name='晴')
            with self.assertRaises(ValueError): library.select(name='不存在')
            with self.assertRaises(ValueError): library.select(track_id='../../secret')


class PlayerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        for name in ('a.mp3','b.mp3'):
            (Path(self.temp.name)/name).write_bytes(b'audio')
        self.conn = NS(config={'plugins':{'play_music':{'music_dir':self.temp.name}}}, stop_event=threading.Event(), chat_lock=threading.Lock(), client_is_speaking=False, tts=NS(tts_text_queue=queue.Queue(),tts_audio_queue=queue.Queue()), sample_rate=24000, logger=Mock(), features={'standby_connection':True})
        self.player = MusicPlayer(self.conn)
        self.conn.local_music = self.player
        self.finished=[]
        async def run():
            self.player.state='playing'
            try: await asyncio.Event().wait()
            finally: self.finished.append(self.player.track['id'])
        self.player.run=run

    async def asyncTearDown(self):
        await self.player.close()
        self.temp.cleanup()

    async def test_pause_resume_and_switch_cancel_previous(self):
        await self.player.command('play',name='a')
        await asyncio.sleep(0)
        self.player.position=12.5
        await self.player.command('pause')
        self.assertEqual(self.player.status()['position'],12.5)
        self.assertEqual(self.player.state,'paused')
        self.assertEqual(len(self.finished),1)
        await self.player.command('resume')
        await asyncio.sleep(0)
        self.assertEqual(self.player.position,12.5)
        await self.player.command('next')
        await asyncio.sleep(0)
        self.assertEqual(self.player.track['name'],'b')
        self.assertEqual(self.player.position,0)
        self.assertEqual(len(self.finished),2)
        await self.player.command('previous')
        await asyncio.sleep(0)
        self.assertEqual(self.player.track['name'],'a')
        await self.player.command('stop')
        self.assertEqual(self.player.state,'stopped')
        self.assertEqual(self.player.position,0)

    async def test_bad_track_preserves_existing_playback(self):
        await self.player.command('play',name='a')
        await asyncio.sleep(0)
        with self.assertRaises(ValueError): await self.player.command('play',name='missing',source='local')
        self.assertEqual(self.player.state,'playing')
        self.assertEqual(self.finished,[])

    async def test_chat_pauses_and_preserves_position(self):
        await self.player.command('play',name='a')
        await asyncio.sleep(0)
        self.player.position=3
        await pause_for_chat(self.conn)
        self.assertEqual(self.player.state,'paused')
        self.assertEqual(self.player.position,3)

    async def test_fast_commands_leave_one_task(self):
        results = await asyncio.gather(*(self.player.command('next') for _ in range(8)), return_exceptions=True)
        self.assertIsInstance(results[-1], dict)
        self.assertTrue(all(isinstance(result, dict) or isinstance(result, ValueError) and '新的操作取消' in str(result) for result in results))
        await asyncio.sleep(0)
        active=[t for t in asyncio.all_tasks() if t.get_name()=='local-music-player' and not t.done()]
        self.assertEqual(len(active),1)

    async def test_disconnect_cancels_player(self):
        await self.player.command('play', name='a')
        await asyncio.sleep(0)
        await self.player.close()
        self.assertEqual(self.player.state, 'stopped')
        self.assertIsNone(self.player.task)
        with self.assertRaises(ValueError):
            await self.player.command('play', name='b')

    async def test_real_decoder_stream_and_lock_cleanup(self):
        target=Path(self.temp.name)/'real.mp3'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.3','-y',str(target)],check=True)
        await validate_mp3(target)
        self.player.track=self.player.library.select(name='real')
        event=asyncio.Event(); event.set()
        self.conn.audio_rate_controller=NS(queue_empty_event=event,reset=Mock())
        self.conn.client_abort=False
        with patch.dict('sys.modules', {'opuslib_next':NS(Encoder=lambda *args:NS(encode=lambda pcm,size:b'opus'),APPLICATION_AUDIO=1)}), patch('core.handle.sendAudioHandle.sendAudio',new_callable=AsyncMock) as send, patch('core.handle.sendAudioHandle.send_tts_message',new_callable=AsyncMock), patch('core.handle.sendAudioHandle._wait_for_audio_completion',new_callable=AsyncMock), patch('core.conversation.standby.begin_interaction'), patch('core.conversation.standby.enter_standby',new_callable=AsyncMock):
            await MusicPlayer.run(self.player)
            self.assertGreater(send.await_count,0)
            self.assertFalse(self.conn.chat_lock.locked())
            self.assertEqual(self.player.state,'stopped')


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_music_plugin_is_awaited_by_executor(self):
        from plugins_func.functions.play_music import play_music
        from core.providers.tools.server_plugins.plugin_executor import ServerPluginExecutor
        conn = NS(config={}, sentence_id='sentence')
        fake = NS(command=AsyncMock(return_value={'name':'歌曲', 'state':'paused'}))
        with patch('plugins_func.functions.play_music.player', return_value=fake):
            result = await ServerPluginExecutor(conn).execute(conn, 'play_music', {'action':'pause'})
            self.assertIn('暂停', result.result)
            fake.command.assert_awaited_once_with('pause', name=None, volume=None, after_sentence='sentence', source=None, artist=None, title=None, mode=None)

    async def test_mode_plugin_calls_player_and_confirms_single_repeat(self):
        from plugins_func.functions.play_music import play_music
        conn = NS(config={}, sentence_id='sentence')
        fake = NS(command=AsyncMock(return_value=dict(name='晴天', mode='single', source='local')))
        with patch('plugins_func.functions.play_music.player', return_value=fake):
            result = await play_music(conn, action='mode', mode='single')
        self.assertIn('单曲循环', result.result)
        fake.command.assert_awaited_once_with('mode', name=None, volume=None, after_sentence='sentence', source=None, artist=None, title=None, mode='single')


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.handler=MusicHandler({'plugins':{'play_music':{'music_dir':self.temp.name}}}, NS(device_handlers={}))
        app=web.Application()
        app.router.add_route('*','/music',self.handler.handle)
        app.router.add_route('POST','/music/control',self.handler.handle)
        app.router.add_route('DELETE','/music/{track_id}',self.handler.handle)
        self.client=TestClient(TestServer(app)); await self.client.start_server()
        self.headers={'Service-Key':'xiaozhi-music'}

    async def asyncTearDown(self):
        await self.client.close(); self.temp.cleanup()

    async def test_auth_and_offline_controls(self):
        self.assertEqual((await self.client.get('/music')).status,401)
        response=await self.client.post('/music/control',headers=self.headers,json={'action':'play','deviceId':'missing'})
        self.assertEqual(response.status,400)

    async def test_invalid_upload_cleanup_and_path(self):
        for name in ('../song.mp3','song.wav','bad?.mp3'):
            response=await self.client.put('/music',params={'name':name},headers=self.headers,data=b'fake')
            self.assertEqual(response.status,400)
        response=await self.client.put('/music',params={'name':'fake.mp3'},headers=self.headers,data=b'fake')
        self.assertEqual(response.status,400)
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])

    async def test_real_upload_list_duplicate_delete(self):
        with tempfile.TemporaryDirectory() as fixture:
            path=Path(fixture)/'sample.mp3'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.15',str(path)],check=True)
            content=path.read_bytes()
        response=await self.client.put('/music',params={'name':'测试.MP3'},headers=self.headers,data=content)
        self.assertEqual(response.status,200,await response.text())
        response=await self.client.get('/music',headers=self.headers)
        tracks=(await response.json())['data']['tracks']
        self.assertEqual(tracks[0]['name'],'测试')
        self.assertNotIn('_path',tracks[0])
        self.assertEqual((await self.client.put('/music',params={'name':'测试.mp3'},headers=self.headers,data=content)).status,400)
        self.assertEqual((await self.client.delete('/music/'+tracks[0]['id'],headers=self.headers)).status,200)
        self.assertEqual(self.handler.library.tracks(),[])

    async def test_size_limit(self):
        with patch('core.api.music_handler.MAX_BYTES',3):
            response=await self.client.put('/music',params={'name':'big.mp3'},headers=self.headers,data=b'1234')
            self.assertEqual(response.status,400)
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])


if __name__=='__main__': unittest.main()
