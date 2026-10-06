"""Timed LCD captions, shared by online songs and downloaded LRC sidecars."""
from bisect import bisect_right
import re


STAMP = re.compile(r'\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]')
OFFSET = re.compile(r'\[offset:\s*([+-]?\d{1,9})\s*\]', re.I)
METADATA = re.compile(r'\[(?:ar|al|ti|by|offset|length|re|ve):[^\]]*\]', re.I)


class LyricsTimeline:
    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        text = data.get('lyric', '')
        self.lines = []
        if isinstance(text, str) and not data.get('instrumental'):
            text = text[:20000]
            offset_match = OFFSET.search(text)
            offset = int(offset_match[1]) / 1000 if offset_match else 0
            seen = set()
            for row in text.splitlines():
                caption = METADATA.sub('', STAMP.sub('', row)).strip()
                # Blank LRC markers denote pauses, not a replacement caption.
                # Keep the previous lyric until the next non-empty timed line.
                if not caption:
                    continue
                for stamp in STAMP.finditer(row):
                    minutes, seconds = int(stamp[1]), int(stamp[2])
                    if seconds >= 60:
                        continue
                    fraction = int(stamp[3]) / 10**len(stamp[3]) if stamp[3] else 0
                    at = max(0, minutes * 60 + seconds + fraction + offset)
                    if (at, caption) not in seen:
                        seen.add((at, caption))
                        self.lines.append((at, caption))
            self.lines.sort(key=lambda line: line[0])
        self.times = [at for at, _ in self.lines]

    def text_at(self, seconds):
        index = bisect_right(self.times, seconds) - 1
        if index < 0:
            return None
        return self.lines[index][1]
