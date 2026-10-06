import unittest
from core.music.lyrics import LyricsTimeline


class LyricsTimelineTests(unittest.TestCase):
    def test_fraction_offset_and_multiple_timestamps_follow_playback_position(self):
        timeline = LyricsTimeline(dict(lyric='[offset:-500]\n[ar:歌手]\n[00:01.50][00:10:500]同一句\n[00:04.125]下一句'))
        self.assertIsNone(timeline.text_at(.99))
        self.assertEqual(timeline.text_at(1), '同一句')
        self.assertEqual(timeline.text_at(3.625), '下一句')
        self.assertEqual(timeline.text_at(10), '同一句')
        # A backward seek must choose by position, not the previously displayed line.
        self.assertEqual(timeline.text_at(2), '同一句')

    def test_empty_timed_lines_keep_previous_lyric_during_instrumental_gap(self):
        timeline = LyricsTimeline(dict(lyric='[00:00]第一句\n[00:02]\n[00:02.25]  \n[00:03]第二句\n[00:05]'))
        self.assertEqual(timeline.text_at(1), '第一句')
        self.assertEqual(timeline.text_at(2.5), '第一句')
        self.assertEqual(timeline.text_at(3), '第二句')
        self.assertEqual(timeline.text_at(6), '第二句')
        # Seeking into an earlier gap uses that gap's lyric, not the last caption.
        self.assertEqual(timeline.text_at(2), '第一句')

    def test_blank_intro_does_not_display_first_lyric_early(self):
        timeline = LyricsTimeline(dict(lyric='[00:00]\n[00:02]第一句'))
        self.assertIsNone(timeline.text_at(1))
        self.assertEqual(timeline.text_at(2), '第一句')

    def test_unsorted_duplicates_and_bad_seconds_do_not_break_sync(self):
        timeline = LyricsTimeline(dict(lyric='[00:05]后一句\n[00:00]开头\n[00:00]开头\n[00:99]无效'))
        self.assertEqual(timeline.lines, [(0, '开头'), (5, '后一句')])

    def test_absent_plain_and_instrumental_lyrics_use_song_caption(self):
        for data in (None, {}, dict(lyric='没有时间戳'), dict(lyric=42), dict(lyric='[00:00]\n[00:02]  '),
                     dict(lyric='[00:00]纯音乐', instrumental=True)):
            with self.subTest(data=data):
                self.assertIsNone(LyricsTimeline(data).text_at(10))


if __name__ == '__main__': unittest.main()
