"""Local MP3 discovery, sidecar metadata and audio-file validation."""
import asyncio
import hashlib
import json
import math
import random
import re
import shutil
import unicodedata
import uuid
from pathlib import Path
from .netease import song_query
from .selection import original_choice, unrequested_variant

MAX_BYTES = 32 * 1024 * 1024


class SongNotFound(ValueError):
    pass

def normalize(value):
    value = unicodedata.normalize('NFKC', value).casefold().strip()
    return re.sub(r'[\s《》“”\"、，。!！?？·_-]+', '', value.removesuffix('.mp3'))

class MusicLibrary:
    def __init__(self, config):
        root = Path(config.get('plugins', {}).get('play_music', {}).get('music_dir', './music'))
        self.root = (root if root.is_absolute() else Path(__file__).resolve().parents[2] / root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def tracks(self):
        result = []
        for path in sorted((p for p in self.root.rglob('*') if p.suffix.lower() == '.mp3'), key=lambda p: str(p).casefold()):
            if path.is_file() and path.resolve().is_relative_to(self.root):
                relative = path.relative_to(self.root).as_posix()
                stem = re.sub(r'-网易云[1-9]\d{0,19}$', '', path.stem)
                artist, separator, title = stem.partition('-')
                if not separator or not artist.strip() or not title.strip():
                    title, artist = stem, ''
                meta = self.metadata(path)
                display_title = meta.get('title') or title.strip()
                display_artist = meta.get('artist') or artist.strip()
                display_name = f'{display_artist} - {display_title}' if meta.get('title') and display_artist else stem
                result.append({'id': hashlib.sha256(relative.encode()).hexdigest()[:24], 'name': display_name,
                    'title': meta.get('title') or title.strip(), 'artist': meta.get('artist') or artist.strip(),
                    'album':meta.get('album', ''), 'cover':meta.get('cover', ''), 'durationMs':meta.get('durationMs', 0),
                    'albumType':meta.get('albumType', ''), 'publishedAt':meta.get('publishedAt', 0),
                    'neteaseId':meta.get('neteaseId', ''), 'size':path.stat().st_size, '_path':path})
        return result

    @staticmethod
    def metadata(path):
        sidecar = path.with_suffix(path.suffix+'.json')
        try:
            if sidecar.stat().st_size>600000: return {}
            data = json.loads(sidecar.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('_size')!=path.stat().st_size or data.get('_mtime')!=path.stat().st_mtime_ns: return {}
            return data
        except (OSError, ValueError, TypeError): return {}

    @staticmethod
    def save_metadata(path, data, audio_path=None):
        audio_path = audio_path or path
        data = dict(data, _size=audio_path.stat().st_size, _mtime=audio_path.stat().st_mtime_ns)
        target = path.with_suffix(path.suffix+'.json')
        temp = target.with_suffix('.tmp-'+uuid.uuid4().hex)
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            temp.replace(target)
        finally: temp.unlink(missing_ok=True)

    def select(self, track_id=None, name=None):
        tracks = self.tracks()
        if not tracks:
            raise SongNotFound('音乐库为空，请先在网页上传 MP3')
        if track_id:
            matches = [t for t in tracks if t['id'] == track_id]
        elif name and name != 'random':
            query = normalize(name)
            query = re.sub(r'^(?:请)?(?:帮我|给我)?(?:播放|放一首|放一下|放)', '', query)
            # Compare title/artist combinations, never split a title on every 的.
            matches = [t for t in tracks if query in {
                normalize(t['name']), normalize(t['title']),
                normalize(t['artist']) + '的' + normalize(t['title']) if t['artist'] else '',
                normalize(t['artist']) + normalize(t['title']) if t['artist'] else '',
                normalize(t['title']) + normalize(t['artist']) if t['artist'] else '',
            }]
            if not matches:
                artist_query = re.sub(r'(?:的歌曲|的音乐|的歌)$', '', query)
                matches = [t for t in tracks if t['artist'] and normalize(t['artist']) == artist_query]
                if matches:
                    return dict(matches[0], _playlist_artist=matches[0]['artist'])
            if not query:
                raise ValueError('请提供歌名或歌手名')
            if not matches:
                matches = [t for t in tracks if query in normalize(t['name'])]
        else:
            return dict(random.choice(tracks), _playback_mode='shuffle') if name == 'random' else tracks[0]
        if not matches:
            raise SongNotFound('没有找到这首音乐，请核对歌名')
        if name and not track_id and len(matches)==1 and unrequested_variant(matches[0], song_query(name).get('title')):
            raise ValueError('本地只匹配到其他录音版本，请确认后播放')
        if len(matches) > 1:
            preferred = original_choice(matches, **song_query(name or '')) if not track_id else None
            if preferred: return preferred
            raise ValueError('匹配到多首音乐，请说完整歌名或在网页选择：' + '、'.join(t['name'] for t in matches[:5]))
        return matches[0]

    def search(self, artist=None, title=None):
        artist = artist or ''; title = title or ''
        if not isinstance(artist, str) or not isinstance(title, str) or max(len(artist),len(title))>100:
            raise ValueError('歌手和歌名每项最多100字')
        artist = normalize(artist); title = normalize(title)
        if not (artist or title): raise ValueError('请至少提供歌名或歌手其中一项')
        return [row for row in self.tracks() if (not artist or artist in normalize(row['artist']))
            and (not title or title in normalize(row['title']))]

async def validate_mp3(path):
    if not shutil.which('ffprobe'):
        raise ValueError('服务器缺少 ffprobe，请安装 FFmpeg')
    process = await asyncio.create_subprocess_exec('ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_name', '-of', 'default=nw=1:nk=1', str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    communication = asyncio.create_task(process.communicate())
    try:
        output, _ = await asyncio.wait_for(asyncio.shield(communication), 10)
        if process.returncode != 0 or output.strip() != b'mp3':
            raise ValueError('文件不是有效的 MP3 音频')
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await asyncio.shield(communication)

async def probe_details(path):
    if not shutil.which('ffprobe'): raise ValueError('服务器缺少 ffprobe，请安装 FFmpeg')
    process = await asyncio.create_subprocess_exec('ffprobe', '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_name:format=duration:format_tags=title,artist,album', '-of', 'json', str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    communication = asyncio.create_task(process.communicate())
    try:
        output,_ = await asyncio.wait_for(asyncio.shield(communication), 10)
        raw = json.loads(output)
        if process.returncode or not raw.get('streams'): raise ValueError('音频无法读取')
        duration = float((raw.get('format') or {}).get('duration', 0))
        tags = (raw.get('format') or {}).get('tags') or {}
        return dict(durationMs=round(duration*1000) if math.isfinite(duration) and duration>0 else 0,
            title=str(tags.get('title', '')), artist=str(tags.get('artist', '')), album=str(tags.get('album', '')),
            codec=raw['streams'][0].get('codec_name'))
    finally:
        if process.returncode is None:
            try: process.kill()
            except ProcessLookupError: pass
        await asyncio.shield(communication)

async def local_details(track):
    meta = MusicLibrary.metadata(track['_path'])
    if not meta.get('durationMs'):
        try:
            details = await probe_details(track['_path'])
            meta = dict(meta, **{key:value for key,value in details.items() if value and key!='codec'})
            MusicLibrary.save_metadata(track['_path'], meta)
        except (ValueError, OSError, TimeoutError, KeyError, TypeError): pass
    return dict(meta, lyric=meta.get('lyric', ''), translation=meta.get('translation', ''), instrumental=bool(meta.get('instrumental')))
