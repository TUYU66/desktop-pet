import tempfile
import unittest
from pathlib import Path
from core.music import MusicLibrary
from core.music.selection import recording_choices, original_choice


def track(key, title='我不难过', artist='孙燕姿', album='未完成', duration=320000, **extra):
    return dict(id='netease:'+str(key), title=title, artist=artist, album=album, durationMs=duration, **extra)


class RecordingSelectionTests(unittest.TestCase):
    def test_studio_duplicate_albums_merge_and_original_album_is_preferred(self):
        original = track(1, albumType='专辑', publishedAt=1000)
        rows = [track(2, album='My Story, Your Song 经典全纪录'),
            track(3, album='2 Her', duration=321000),
            track(4, title='我不难过 (Live)', album='2010音乐嘉年华'), original,
            track(5, album='经典全纪录 主打精华版')]
        choices = recording_choices(rows)
        self.assertEqual([row['id'] for row in choices], ['netease:1','netease:4'])
        self.assertEqual(original_choice(rows, artist='孙燕姿', title='我不难过', has_more=True), original)

    def test_different_singers_and_recordings_are_not_equivalent(self):
        for other in (track(2, artist='其他歌手'), track(2, duration=340000),
                track(2, album='重新录制'), track(2, title='我不难过 Remix')):
            self.assertEqual(len(recording_choices([track(1), other])), 2)
        self.assertIsNone(original_choice([track(1), track(2, artist='其他歌手')], title='我不难过'))
        self.assertIsNone(original_choice([track(1), track(2, duration=340000)], title='我不难过', artist='孙燕姿'))

    def test_unknown_duration_or_partial_query_does_not_prove_same_recording(self):
        self.assertEqual(len(recording_choices([track(1, duration=0), track(2, duration=0)])), 2)
        self.assertIsNone(original_choice([track(1)], title='我不', artist='孙燕姿'))
        self.assertIsNone(original_choice([track(1)], title='我不难过', has_more=True))

    def test_live_request_is_respected_and_concerts_are_not_merged(self):
        live = track(1, title='我不难过 (Live)', album='2010现场演唱会')
        later = track(2, title='我不难过 (Live)', album='2020现场演唱会')
        self.assertIsNone(original_choice([live], title='我不难过', artist='孙燕姿'))
        self.assertEqual(original_choice([live], title='我不难过 (Live)', artist='孙燕姿'), live)
        self.assertEqual(len(recording_choices([live,later])), 2)


class LocalDisplayNameTests(unittest.TestCase):
    def test_download_number_is_not_in_names_even_without_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'孙燕姿-我不难过-网易云466343434.mp3'
            path.write_bytes(b'audio')
            library = MusicLibrary(dict(plugins=dict(play_music=dict(music_dir=folder))))
            before = library.tracks()[0]
            self.assertEqual(before['title'], '我不难过')
            self.assertNotIn('网易云466343434', before['name'])
            MusicLibrary.save_metadata(path, dict(title='我不难过', artist='孙燕姿', neteaseId='466343434'))
            after = library.tracks()[0]
            self.assertEqual(after['name'], '孙燕姿 - 我不难过')
            self.assertEqual(after['id'], before['id'])
            self.assertEqual(after['_path'], path)
            self.assertTrue(path.exists())

    def test_local_duplicate_album_choice_keeps_original_file(self):
        with tempfile.TemporaryDirectory() as folder:
            library = MusicLibrary(dict(plugins=dict(play_music=dict(music_dir=folder))))
            for key,album in [(1,'未完成'), (2,'精选全纪录')]:
                path = Path(folder)/f'孙燕姿-我不难过-网易云{key}.mp3'
                path.write_bytes(b'audio')
                MusicLibrary.save_metadata(path, dict(title='我不难过', artist='孙燕姿', album=album, durationMs=320000))
            selected = library.select(name='孙燕姿的我不难过')
            self.assertEqual(selected['album'], '未完成')
            self.assertEqual(len(library.tracks()), 2)


if __name__ == '__main__': unittest.main()
