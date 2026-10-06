"""Account-bound NetEase reads and playable URLs; no credentials in HTTP results."""
import asyncio
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]


def song_id(value):
    value = str(value).removeprefix('netease:')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', value):
        raise ValueError('网易云歌曲或歌单编号无效')
    return value


def match_text(value):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', value).casefold())


def liked_playlist_name(value):
    """User-defined aliases for their own red-heart playlist, not subscriptions."""
    if not isinstance(value, str): return False
    if value.strip().startswith(('《', '“', '"')): return False
    value = re.sub(r'^(?:我的|网易云(?:音乐)?的?)+', '', match_text(value))
    return value in {'红心歌单', '红心的歌单', '红心音乐', '红心歌曲', '收藏歌单', '收藏的歌单',
        '我收藏歌单', '我收藏的歌单', '我喜欢', '我喜欢的歌单',
        '我喜欢的音乐', '我喜欢的歌曲', '我喜欢的歌', '喜欢的歌单', '喜欢的音乐', '喜欢的歌曲', '喜欢的歌'}


def track_info(raw):
    artists = raw.get('ar') or raw.get('artists') or []
    album = raw.get('al') or raw.get('album') or {}
    artist = ' / '.join(str(item.get('name', '')) for item in artists)
    title = str(raw.get('name', ''))
    return dict(id='netease:'+song_id(raw['id']), title=title, artist=artist,
        name=f'{artist} - {title}', album=str(album.get('name', '')),
        cover=str(album.get('picUrl', '')), durationMs=int(raw.get('dt') or raw.get('duration') or 0),
        albumType=str(album.get('type') or ''), publishedAt=album.get('publishTime') or raw.get('publishTime') or 0,
        source='netease', size=0)


def split_song_name(value):
    value = re.sub(r'^(?:用|在)?网易云(?:音乐)?(?:播放)?', '', value.strip())
    match = re.fullmatch(r'(.+?)(?:的|\s*[-—]\s*)[《“"]?(.+?)[》”"]?', value)
    if not match:
        return None
    artist, title = match[1].strip(), match[2].strip('《》“”" ')
    return (artist, title) if artist and title and title not in ('歌', '歌曲', '音乐') else None


def song_query(value):
    """Extract only the song/artist conditions actually supplied in a music request."""
    value = value.strip()
    if re.fullmatch(r'(?:《.+》|“.+”|".+")', value): return dict(title=value.strip('《》“”" '))
    title = re.fullmatch(r'(?:歌名|歌曲)\s*(.+)', value)
    if title: return dict(title=title[1].strip('《》“”" '))
    explicit_artist = re.fullmatch(r'(?:歌手|演唱者)\s*(.+)', value)
    if explicit_artist: value = explicit_artist[1].strip()
    artist = re.fullmatch(r'(.+?)的(?:歌曲|音乐|歌)', value)
    if artist: return dict(artist=artist[1].strip('《》“”" '))
    pair = split_song_name(value)
    if pair: return dict(artist=pair[0], title=pair[1])
    return dict(artist=value) if explicit_artist else dict(title=value)


class NeteaseClient:
    def __init__(self, config):
        self.bridge = ROOT/'netease-api'/'call.cjs'
        self.storage = ROOT/'data'/'netease-account.json'
        self.node = os.environ.get('XIAOZHI_NETEASE_NODE') or shutil.which('node')
        self.cookie = ''
        self.profile = None
        self.generation = 0
        self.qr = None
        self.playlist_cache = {}
        self.tracks = {}
        self.lyric_cache = {}
        self.slots = asyncio.Semaphore(3)
        self.checked = 0
        try:
            saved = json.loads(self.storage.read_text(encoding='utf-8'))
            if isinstance(saved, dict) and isinstance(saved.get('cookie'), str) and isinstance(saved.get('profile'), dict):
                self.cookie, self.profile = saved['cookie'], saved['profile']
        except (OSError, ValueError, TypeError):
            pass

    def ready(self):
        return bool(self.node and (self.bridge.parent/'node_modules'/'@neteasecloudmusicapienhanced'/'api'/'package.json').is_file())

    def require_login(self):
        if not self.cookie or not self.profile:
            raise ValueError('请先在音乐空间扫码登录网易云账号')

    def persist(self, cookie=None, profile=None):
        self.storage.parent.mkdir(parents=True, exist_ok=True)
        temp = self.storage.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as output:
            if sys.platform != 'win32': os.chmod(temp, 0o600)
            json.dump(dict(cookie=self.cookie if cookie is None else cookie,
                profile=self.profile if profile is None else profile), output, ensure_ascii=False)
        temp.replace(self.storage)

    async def call(self, method, cookie=None, **params):
        if not self.ready():
            raise ValueError('网易云组件未安装，请在 xiaozhi-python-server/netease-api 中执行 npm install')
        params['cookie'] = self.cookie if cookie is None else cookie
        generation = self.generation
        async with self.slots:
            process = await asyncio.create_subprocess_exec(self.node, str(self.bridge),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                cwd=str(self.bridge.parent), **({'creationflags': 0x08000000} if sys.platform == 'win32' else {}))
            try:
                output, _ = await asyncio.wait_for(process.communicate(json.dumps(dict(method=method, params=params)).encode()), 23)
                if process.returncode or len(output) > 8*1024*1024:
                    raise ValueError('网易云接口未返回有效结果，请重试')
                result = json.loads(output)
            except asyncio.TimeoutError:
                raise ValueError('网易云请求超时，请检查网络后重试') from None
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError('网易云接口响应异常，请重试') from None
            finally:
                if process.returncode is None:
                    try: process.kill()
                    except ProcessLookupError: pass
                    try:
                        await asyncio.wait_for(process.communicate(), 3)
                    except (OSError, asyncio.TimeoutError):
                        pass  # The killed bridge must not keep a control/slot occupied.
        if not isinstance(result, dict): raise ValueError('网易云接口响应异常，请重试')
        if not result.get('ok'):
            if result.get('kind') == 'timeout': raise ValueError('网易云请求超时，请检查网络后重试')
            if result.get('code') in (301, 302):
                if cookie is None and generation == self.generation: self.forget()
                raise ValueError('网易云登录已失效，请重新扫码登录')
            raise ValueError('网易云接口暂时不可用，请检查网络或稍后重试')
        body = result.get('body')
        if not isinstance(body, dict): raise ValueError('网易云返回的数据格式异常')
        if body.get('code') in (301, 302) and cookie is None and generation == self.generation: self.forget()
        return body

    @staticmethod
    def checked_body(body):
        if body.get('code') in (301, 302): raise ValueError('网易云登录已失效，请重新扫码登录')
        if body.get('code') != 200: raise ValueError('网易云没有完成这次请求，请稍后重试')
        return body

    def current(self, generation):
        if generation != self.generation: raise ValueError('网易云账号已变化，请重新操作')

    async def account(self, verify=False):
        if not self.ready():
            return dict(ready=False, loggedIn=False, profile=None, message='网易云组件尚未安装')
        if self.cookie and (verify or time.monotonic()-self.checked > 60):
            generation = self.generation
            try: body = await self.call('login_status')
            except ValueError:
                if not self.cookie and generation != self.generation:
                    return dict(ready=True, loggedIn=False, profile=None, message='网易云登录已失效，请重新扫码')
                raise
            self.current(generation)
            data = body.get('data', body)
            raw = data.get('profile') or {}
            if not raw.get('userId'):
                self.forget()
                return dict(ready=True, loggedIn=False, profile=None, message='网易云登录已失效，请重新扫码')
            self.profile = dict(userId=song_id(raw['userId']), nickname=str(raw.get('nickname', '网易云用户')),
                avatar=str(raw.get('avatarUrl', '')))
            self.checked = time.monotonic()
        return dict(ready=True, loggedIn=bool(self.cookie and self.profile), profile=self.profile)

    def forget(self):
        self.generation += 1
        self.cookie = ''; self.profile = None; self.qr = None; self.checked = 0
        self.playlist_cache.clear(); self.tracks.clear(); self.lyric_cache.clear()
        self.storage.unlink(missing_ok=True)

    async def create_qr(self):
        generation = self.generation
        key = self.checked_body(await self.call('login_qr_key', cookie='', type=1)).get('data', {}).get('unikey')
        if not isinstance(key, str) or not key: raise ValueError('网易云二维码生成失败，请重试')
        body = self.checked_body(await self.call('login_qr_create', cookie='', key=key, qrimg=True))
        self.current(generation)
        image = body.get('data', {}).get('qrimg', '')
        if not isinstance(image, str) or not re.fullmatch(r'data:image/png;base64,[A-Za-z0-9+/=]+', image):
            raise ValueError('网易云二维码图片无效，请重试')
        self.qr = dict(token=uuid.uuid4().hex, key=key, expires=time.monotonic()+180)
        return dict(token=self.qr['token'], image=image, expiresIn=180)

    async def check_qr(self, token):
        pending = self.qr
        if not pending or pending['token'] != token or pending['expires'] < time.monotonic():
            return dict(state='expired')
        generation = self.generation
        body = await self.call('login_qr_check', cookie='', key=pending['key'], noCookie=True)
        self.current(generation)
        if self.qr is not pending: return dict(state='expired')
        code = body.get('code')
        if code == 803:
            raw = body.get('cookie', '')
            cookies = dict(re.findall(r'(?:^|[;,]\s*)(MUSIC_U|MUSIC_A|__csrf|NMTID)=([^;,\s]+)', raw)) if isinstance(raw, str) else {}
            if not cookies.get('MUSIC_U'): raise ValueError('网易云授权未返回登录信息，请重新扫码')
            cookie = '; '.join(f'{key}={value}' for key,value in cookies.items())
            account = await self.call('login_status', cookie=cookie)
            self.current(generation)
            if self.qr is not pending: return dict(state='expired')
            profile = account.get('data', account).get('profile') or {}
            if not profile.get('userId'): raise ValueError('网易云账号身份未确认，请重新扫码')
            profile = dict(userId=song_id(profile['userId']), nickname=str(profile.get('nickname', '网易云用户')),
                avatar=str(profile.get('avatarUrl', '')))
            self.persist(cookie, profile)
            self.cookie = cookie; self.profile = profile
            self.generation += 1; self.qr = None; self.checked = time.monotonic()
            self.playlist_cache.clear(); self.tracks.clear(); self.lyric_cache.clear()
            return dict(state='authorized', account=await self.account())
        if code not in (800, 801, 802): raise ValueError('网易云扫码状态查询失败，请重试')
        return dict(state={800:'expired', 801:'waiting', 802:'confirming'}[code])

    async def playlists(self, offset=0, limit=30):
        self.require_login(); generation=self.generation
        body = self.checked_body(await self.call('user_playlist', uid=self.profile['userId'], offset=offset, limit=limit))
        self.current(generation)
        items = [dict(id=song_id(p['id']), name=str(p.get('name', '歌单')), cover=str(p.get('coverImgUrl', '')),
            trackCount=int(p.get('trackCount', 0)), created=str((p.get('creator') or {}).get('userId') or p.get('userId')) == self.profile['userId'],
            liked=str(p.get('specialType', '')) == '5')
            for p in body.get('playlist', [])]
        return dict(items=items, hasMore=bool(body.get('more')), nextOffset=offset+len(items))

    async def find_playlists(self, name=None, liked=False):
        self.require_login(); generation = self.generation
        if not liked and (not isinstance(name, str) or len(name)>100 or not match_text(name)):
            raise ValueError('请提供最多100字的歌单名称')

        async def scan():
            rows = {}; offset = 0
            for _ in range(10):
                page = await self.playlists(offset=offset, limit=100)
                self.current(generation)
                rows.update({row['id']:row for row in page['items']})
                # The marked, account-owned liked playlist has a stable identity,
                # independent of nickname changes and ordinary playlist names.
                if liked:
                    marked = [row for row in rows.values() if row['created'] and row.get('liked')]
                    if marked: return marked
                if not page['hasMore']:
                    break
                if page['nextOffset'] <= offset:
                    raise ValueError('网易云歌单分页未完成，请在网页刷新后重试')
                offset = page['nextOffset']
            else:
                raise ValueError('账号歌单较多，请在网页选择要播放的歌单')
            self.current(generation)
            if liked:
                # Older responses can omit specialType. Only use an exact owned
                # default-name match; never guess from arbitrary liked substrings.
                names = {match_text('我喜欢的音乐'), match_text(str(self.profile.get('nickname', ''))+'喜欢的音乐')}
                return [row for row in rows.values() if row['created'] and match_text(row['name']) in names]
            query = match_text(name)
            exact = [row for row in rows.values() if match_text(row['name']) == query]
            return exact or [row for row in rows.values() if query in match_text(row['name'])]

        try:
            return await asyncio.wait_for(scan(), timeout=20)
        except asyncio.TimeoutError:
            raise ValueError('读取网易云歌单超时，请稍后重试或在网页选择') from None

    async def playlist(self, value):
        self.require_login(); generation=self.generation; key=song_id(value)
        cached = self.playlist_cache.get(key)
        if cached and cached['expires'] > time.monotonic(): return cached
        body = self.checked_body(await self.call('playlist_detail', id=key))
        self.current(generation)
        raw = body.get('playlist') or {}
        if song_id(raw.get('id', '')) != key: raise ValueError('网易云歌单回执不匹配')
        if not isinstance(raw.get('trackIds'), list): raise ValueError('网易云未返回完整歌单，请稍后刷新')
        ids = [song_id(item['id']) for item in raw.get('trackIds', [])]
        # Preserve order, including the full song IDs rather than a partial tracks field.
        cached = dict(id=key, name=str(raw.get('name', '歌单')), ids=list(dict.fromkeys(ids)), expires=time.monotonic()+120)
        self.playlist_cache[key] = cached
        return cached

    async def songs(self, ids):
        self.require_login(); generation=self.generation
        ids = [song_id(value) for value in ids]
        if not ids: return []
        body = self.checked_body(await self.call('song_detail', ids=','.join(ids)))
        self.current(generation)
        result = {song_id(raw['id']):track_info(raw) for raw in body.get('songs', [])}
        self.tracks.update({item['id']: item for item in result.values()})
        return [result[value] for value in ids if value in result]

    async def playlist_songs(self, value, offset=0, limit=50, query=''):
        generation = self.generation
        playlist = await self.playlist(value)
        if not isinstance(query, str) or len(query)>100: raise ValueError('歌单检索最多100字')
        if query.strip() and not match_text(query): raise ValueError('请输入歌名、歌手或专辑关键词')
        scan = 200 if query.strip() else limit
        items = await self.songs(playlist['ids'][offset:offset+scan])
        if query.strip():
            terms = [match_text(term) for term in query.split() if match_text(term)]
            items = [row for row in items if all(term in match_text(row['title']+' '+row['artist']+' '+row['album']) for term in terms)]
        self.current(generation)
        return dict(items=items, total=len(playlist['ids']), nextOffset=min(offset+scan, len(playlist['ids'])),
            hasMore=offset+scan < len(playlist['ids']), name=playlist['name'])

    async def search(self, artist=None, title=None, offset=0, limit=30):
        self.require_login(); generation=self.generation
        artist = '' if artist is None else artist
        title = '' if title is None else title
        if not isinstance(artist, str) or not isinstance(title, str) or max(len(artist),len(title))>100:
            raise ValueError('歌手和歌名每项最多100字')
        artist = artist.strip(); title = title.strip()
        if not (artist or title) or any(value and not match_text(value) for value in (artist,title)):
            raise ValueError('请至少填写歌手或歌名其中一项')
        body = self.checked_body(await self.call('cloudsearch', keywords=' '.join(value for value in (artist,title) if value), type=1, limit=limit, offset=offset))
        self.current(generation)
        result = body.get('result') or {}
        raw = result.get('songs') or []
        items = [track_info(song) for song in raw]
        items = [item for item in items if (not artist or match_text(artist) in match_text(item['artist'])) and (not title or match_text(title) in match_text(item['title']))]
        self.tracks.update({item['id']:item for item in items})
        total = int(result.get('songCount', 0))
        return dict(items=items, candidateTotal=total, nextOffset=offset+len(raw), hasMore=bool(raw) and offset+len(raw)<total)

    async def track(self, value):
        key = 'netease:'+song_id(value)
        self.require_login()
        item = self.tracks.get(key)
        if item: return dict(item)
        items = await self.songs([value])
        if not items: raise ValueError('网易云未找到这首歌曲')
        return items[0]

    async def play_url(self, value):
        self.require_login(); generation=self.generation; key=song_id(value)
        body = self.checked_body(await self.call('song_url_v1', id=key, level='exhigh', unblock='false'))
        self.current(generation)
        entry = next((row for row in body.get('data', []) if str(row.get('id'))==key), None)
        return self.audio_url(entry)

    @staticmethod
    def audio_url(entry):
        if not entry or not entry.get('url'): raise ValueError('网易云未提供完整音源，请核实账号权限或歌曲状态')
        if entry.get('freeTrialInfo') not in (None, False, '', 'null'):
            raise ValueError('网易云仅返回试听片段，未开始完整播放，请核实账号权限')
        url = entry['url']
        if not isinstance(url, str): raise ValueError('网易云音源地址无效')
        parsed=urlparse(url)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in ('http','https') or parsed.username or parsed.password or not any(host==domain or host.endswith('.'+domain) for domain in ('music.126.net','music.163.com')):
            raise ValueError('网易云音源地址未通过校验')
        return url

    async def download_url(self, value):
        self.require_login(); generation=self.generation; key=song_id(value)
        body = self.checked_body(await self.call('song_download_url_v1', id=key, level='exhigh'))
        self.current(generation)
        data = body.get('data')
        entry = next((row for row in data if str(row.get('id'))==key), None) if isinstance(data, list) else data
        if not isinstance(entry, dict) or str(entry.get('id', key))!=key: raise ValueError('网易云下载回执不匹配')
        return self.audio_url(entry)

    async def lyrics(self, value):
        self.require_login(); generation=self.generation; key=song_id(value)
        cached = self.lyric_cache.get(key)
        if cached and cached['expires']>time.monotonic(): return dict(cached['data'])
        body = self.checked_body(await self.call('lyric', id=key))
        self.current(generation)
        def text(field):
            value = (body.get(field) or {}).get('lyric', '')
            # Two lyric fields must also fit the Java proxy's default response buffer
            # when the HTTP JSON serializer escapes non-ASCII characters.
            return value[:20000] if isinstance(value, str) else ''
        data = dict(lyric=text('lrc'), translation=text('tlyric'), instrumental=bool(body.get('nolyric')))
        self.lyric_cache[key] = dict(data=data, expires=time.monotonic()+300)
        return data


_client = None


def netease(config=None):
    global _client
    if _client is None: _client = NeteaseClient(config or {})
    return _client
