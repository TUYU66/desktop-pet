"""Import authorized full downloads into the existing local MP3 library."""
import asyncio
import re
import shutil
import uuid
from urllib.parse import urljoin
import aiohttp
from core.music.library import MAX_BYTES, MusicLibrary, probe_details
from core.music.netease import NeteaseClient, song_id


async def fetch_audio(url, path):
    timeout = aiohttp.ClientTimeout(total=80, connect=10, sock_read=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout, auto_decompress=False) as session:
            for _ in range(4):
                NeteaseClient.audio_url(dict(url=url))
                async with session.get(url, allow_redirects=False, headers={'Accept-Encoding':'identity'}) as response:
                    if response.status in (301,302,303,307,308):
                        location = response.headers.get('Location')
                        if not location: raise ValueError('网易云下载跳转无效')
                        url = urljoin(url, location)
                        continue
                    if response.status != 200: raise ValueError('网易云下载音源暂不可用，请稍后重试')
                    length = response.content_length
                    if length and length>MAX_BYTES: raise ValueError('单首下载音源超过32 MB，请选择较短的歌曲')
                    size = 0
                    with path.open('xb') as output:
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size>MAX_BYTES: raise ValueError('单首下载音源超过32 MB')
                            output.write(chunk)
                    if not size or length is not None and length!=size: raise ValueError('歌曲下载不完整，请重新下载')
                    return
            raise ValueError('网易云下载跳转次数过多，请稍后重试')
    except (aiohttp.ClientError, TimeoutError):
        raise ValueError('歌曲下载中断或超时，请检查网络后重试') from None


async def convert_mp3(source, target):
    if not shutil.which('ffmpeg'): raise ValueError('服务器缺少 FFmpeg，无法转换下载音源')
    process = await asyncio.create_subprocess_exec('ffmpeg', '-nostdin', '-v', 'error', '-i', str(source),
        '-map', '0:a:0', '-vn', '-codec:a', 'libmp3lame', '-b:a', '192k', '-f', 'mp3', str(target),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(process.wait(), 40)
        if process.returncode: raise ValueError('下载音源无法转成 MP3，请检查 FFmpeg 或选择其他版本')
    except TimeoutError:
        raise ValueError('歌曲转换超时，请稍后重试') from None
    finally:
        if process.returncode is None:
            try: process.kill()
            except ProcessLookupError: pass
            await process.wait()


async def import_song(client, library, value, mutation_lock=None):
    mutation_lock = mutation_lock if mutation_lock is not None else asyncio.Lock()
    client.require_login(); generation=client.generation; key=song_id(value)
    existing = next((row for row in library.tracks() if row.get('neteaseId')==key), None)
    if existing: return dict(track={k:v for k,v in existing.items() if k!='_path'}, alreadyPresent=True)
    track = await client.track(value)
    url = await client.download_url(value)
    client.current(generation)
    safe = lambda value: re.sub(r'[\\/<>:"|?*\x00-\x1f]', ' ', value).strip(' .')
    name = f'{safe(track["artist"])[:40] or "网易云"}-{safe(track["title"])[:70] or "歌曲"}-网易云{key}.mp3'
    target = library.root/name
    if target.exists(): raise ValueError('本地已存在同名文件，请先核对本地音乐库')
    raw = library.root/('.download-'+uuid.uuid4().hex)
    converted = library.root/('.converted-'+uuid.uuid4().hex)
    committed = False; sidecar_written = False
    try:
        await fetch_audio(url, raw)
        client.current(generation)
        details = await probe_details(raw)
        if details['codec']=='mp3': raw.rename(converted)
        else:
            await convert_mp3(raw, converted)
            details = await probe_details(converted)
        if converted.stat().st_size>MAX_BYTES: raise ValueError('转换后的 MP3 超过32 MB，未加入本地库')
        duration = details['durationMs']; expected = track.get('durationMs', 0)
        if duration<=0 or expected and abs(duration-expected)>max(3000, expected*.03):
            raise ValueError('下载音频时长不完整或与歌曲不符，未加入本地库')
        lyrics = {}; warning = ''
        try: lyrics = await client.lyrics(value)
        except ValueError: warning = '歌曲已下载，歌词暂未取得'
        # Network requests, audio transfer, conversion and lyrics must not hold the
        # library lock needed by uploads/deletes. Recheck conflicts at publication.
        async with mutation_lock:
            client.current(generation)
            existing = next((row for row in library.tracks() if row.get('neteaseId')==key), None)
            if existing: return dict(track={k:v for k,v in existing.items() if k!='_path'}, alreadyPresent=True)
            if target.exists(): raise ValueError('本地已存在同名文件，请先核对本地音乐库')
            meta = dict(title=track['title'], artist=track['artist'], album=track.get('album', ''),
                albumType=track.get('albumType', ''), publishedAt=track.get('publishedAt', 0),
                cover=track.get('cover', ''), durationMs=duration, neteaseId=key, **lyrics)
            MusicLibrary.save_metadata(target, meta, converted); sidecar_written = True
            converted.rename(target); committed = True
            saved = next(row for row in library.tracks() if row['_path']==target)
            return dict(track={k:v for k,v in saved.items() if k!='_path'}, alreadyPresent=False, warning=warning)
    finally:
        raw.unlink(missing_ok=True); converted.unlink(missing_ok=True)
        if sidecar_written and not committed: target.with_suffix('.mp3.json').unlink(missing_ok=True)
