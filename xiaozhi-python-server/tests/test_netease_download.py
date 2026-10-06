import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace as NS
from core.api.music_handler import MusicHandler
from core.api.netease_handler import NeteaseHandler
from core.music import MusicLibrary
from core.music.download import import_song


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = MusicLibrary(dict(plugins=dict(play_music=dict(music_dir=self.temp.name))))
        self.client = Mock(generation=1)
        self.client.track = AsyncMock(return_value=dict(id='netease:1', title='晴天', artist='周杰伦',
            album='叶惠美', cover='https://p1.music.126.net/cover.jpg', durationMs=269000))
        self.client.download_url = AsyncMock(return_value='https://m.music.126.net/song.mp3')
        self.client.lyrics = AsyncMock(return_value=dict(lyric='[00:01]故事的小黄花', translation='', instrumental=False))
        async def fetch(url, path): path.write_bytes(b'complete-audio')
        self.fetcher = patch('core.music.download.fetch_audio', new=AsyncMock(side_effect=fetch))
        self.prober = patch('core.music.download.probe_details', new=AsyncMock(return_value=dict(codec='mp3', durationMs=269000)))
        self.fetch = self.fetcher.start(); self.probe = self.prober.start()

    async def asyncTearDown(self):
        self.fetcher.stop(); self.prober.stop(); self.temp.cleanup()

    async def test_complete_download_preserves_metadata_lyrics_and_deduplicates(self):
        result = await import_song(self.client, self.library, 'netease:1')
        self.assertFalse(result['alreadyPresent'])
        self.assertNotIn('_path', result['track'])
        row = self.library.tracks()[0]
        self.assertEqual((row['title'], row['artist'], row['album'], row['neteaseId']), ('晴天','周杰伦','叶惠美','1'))
        self.assertEqual(row['_path'].read_bytes(), b'complete-audio')
        self.assertEqual(MusicLibrary.metadata(row['_path'])['lyric'], '[00:01]故事的小黄花')
        self.assertEqual(len(list(self.library.root.iterdir())), 2)
        self.assertTrue((await import_song(self.client, self.library, '1'))['alreadyPresent'])
        self.fetch.assert_awaited_once()

    async def test_short_audio_never_becomes_visible_in_library(self):
        self.probe.return_value = dict(codec='mp3', durationMs=30000)
        with self.assertRaisesRegex(ValueError, '时长不完整'):
            await import_song(self.client, self.library, '1')
        self.assertEqual(list(self.library.root.iterdir()), [])

    async def test_interrupted_transfer_removes_partial_file(self):
        async def interrupted(url, path):
            path.write_bytes(b'partial')
            raise ValueError('下载中断')
        self.fetch.side_effect = interrupted
        with self.assertRaisesRegex(ValueError, '下载中断'):
            await import_song(self.client, self.library, '1')
        self.assertEqual(list(self.library.root.iterdir()), [])

    async def test_account_change_before_commit_discards_download(self):
        self.client.current.side_effect = [None, None, ValueError('账号已变化')]
        with self.assertRaisesRegex(ValueError, '账号已变化'):
            await import_song(self.client, self.library, '1')
        self.assertEqual(list(self.library.root.iterdir()), [])

    async def test_lyric_failure_keeps_complete_song_with_warning(self):
        self.client.lyrics.side_effect = ValueError('歌词请求失败')
        result = await import_song(self.client, self.library, '1')
        self.assertIn('歌词', result['warning'])
        self.assertEqual(len(self.library.tracks()), 1)

    async def test_other_audio_format_converts_before_publishing(self):
        self.probe.side_effect = [dict(codec='flac', durationMs=269000), dict(codec='mp3', durationMs=269000)]
        async def convert(raw, target): target.write_bytes(b'converted-mp3')
        with patch('core.music.download.convert_mp3', new=AsyncMock(side_effect=convert)) as conversion:
            await import_song(self.client, self.library, '1')
            conversion.assert_awaited_once()
        self.assertEqual(self.library.tracks()[0]['_path'].read_bytes(), b'converted-mp3')
        self.assertEqual(len(list(self.library.root.iterdir())), 2)

    async def test_conversion_failure_removes_all_temporary_files(self):
        self.probe.return_value = dict(codec='flac', durationMs=269000)
        async def failed(raw, target):
            target.write_bytes(b'partial-conversion')
            raise ValueError('转换失败')
        with patch('core.music.download.convert_mp3', new=AsyncMock(side_effect=failed)):
            with self.assertRaisesRegex(ValueError, '转换失败'):
                await import_song(self.client, self.library, '1')
        self.assertEqual(list(self.library.root.iterdir()), [])

    async def test_deleting_another_song_during_download_does_not_wait_for_network(self):
        local = self.library.root/'已有歌曲.mp3'
        local.write_bytes(b'local-audio')
        local_id = self.library.tracks()[0]['id']
        music_handler = MusicHandler(dict(plugins=dict(play_music=dict(music_dir=self.temp.name))), None)
        with patch('core.api.netease_handler.netease', return_value=self.client):
            handler = NeteaseHandler({}, None, music_handler)
        started, resume = asyncio.Event(), asyncio.Event()
        async def slow_fetch(url, path):
            started.set()
            await resume.wait()
            path.write_bytes(b'complete-audio')
        self.fetch.side_effect = slow_fetch
        request = NS(method='POST', match_info=dict(operation='download'), json=AsyncMock(return_value=dict(trackId='1')))
        delete = NS(method='DELETE', match_info=dict(track_id=local_id))
        with patch.object(MusicHandler, 'authorized', return_value=True):
            download = asyncio.create_task(handler.handle(request))
            try:
                await asyncio.wait_for(started.wait(), 1)
                self.assertFalse(music_handler.mutation_lock.locked())
                response = await asyncio.wait_for(music_handler.handle(delete), 1)
                self.assertEqual(json.loads(response.text)['code'], 0)
                self.assertFalse(local.exists())
                self.assertFalse(download.done())
                resume.set()
                response = await asyncio.wait_for(download, 1)
                self.assertEqual(json.loads(response.text)['code'], 0)
            finally:
                resume.set()
                download.cancel()
                await asyncio.gather(download, return_exceptions=True)
        self.assertEqual(len(self.library.tracks()), 1)

    async def test_upload_conflict_during_transfer_cannot_be_overwritten(self):
        target = self.library.root/'周杰伦-晴天-网易云1.mp3'
        async def concurrent_upload(url, path):
            path.write_bytes(b'complete-audio')
            target.write_bytes(b'manually-uploaded')
        self.fetch.side_effect = concurrent_upload
        with self.assertRaisesRegex(ValueError, '同名文件'):
            await import_song(self.client, self.library, '1', asyncio.Lock())
        self.assertEqual(target.read_bytes(), b'manually-uploaded')
        self.assertEqual(list(self.library.root.iterdir()), [target])

    async def test_duplicate_created_during_transfer_is_not_imported_twice(self):
        target = self.library.root/'已有网易云歌曲.mp3'
        async def concurrent_import(url, path):
            path.write_bytes(b'complete-audio')
            target.write_bytes(b'existing-download')
            MusicLibrary.save_metadata(target, dict(neteaseId='1', title='晴天'))
        self.fetch.side_effect = concurrent_import
        result = await import_song(self.client, self.library, '1', asyncio.Lock())
        self.assertTrue(result['alreadyPresent'])
        self.assertEqual(target.read_bytes(), b'existing-download')
        self.assertEqual(len(list(self.library.root.iterdir())), 2)


if __name__ == '__main__': unittest.main()
