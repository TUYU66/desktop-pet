"""Playback sessions, device audio output and interruption handoffs."""
import asyncio
import copy
from array import array
import math
import random
import re
import shutil
import sys
import time
import uuid
from .library import MusicLibrary, SongNotFound, normalize, local_details
from .netease import netease, song_id, song_query, liked_playlist_name
from .selection import recording_choices, original_choice, unrequested_variant
from .lyrics import LyricsTimeline

PLAYBACK_MODES = {'sequence':'顺序播放', 'shuffle':'随机播放', 'single':'单曲循环'}


class MusicPlayer:
    def __init__(self, conn):
        self.conn = conn
        self.library = MusicLibrary(conn.config)
        self.track = None
        self.playlist_artist = None
        self.position = 0.0
        self.state = 'stopped'
        self.error = ''
        self.task = None
        self.stream_id = None
        self.playback_stop = asyncio.Event()
        self.preparing = None
        self.preparing_source = None
        self.command_revision = 0
        self.lock = asyncio.Lock()
        self.closing = False
        self.volume = 70
        self.interrupted_for_chat = False
        self.awaiting_sentence = None
        self.speech_ready = asyncio.Event()
        self.speech_ready.set()
        self.mode = 'sequence'
        self.shuffle_queue = []
        self.shuffle_played = set()
        self.history = []
        self.history_index = -1
        self.pending_history_index = None
        self.source = 'local'
        self.online_queue = []
        self.online_generation = None
        self.online_playlist_id = None
        self.online_playlist_name = ''
        self.sessions = {}

    SESSION_FIELDS = ('track', 'playlist_artist', 'position', 'state', 'error', 'volume',
        'mode', 'shuffle_queue', 'shuffle_played', 'history', 'history_index',
        'pending_history_index', 'online_queue', 'online_generation', 'online_playlist_id', 'online_playlist_name')

    def saved_status(self, session):
        track = session.get('track') or {}
        return dict(state=session.get('state', 'stopped'), trackId=track.get('id'),
            name=track.get('name', ''), title=track.get('title', ''), artist=track.get('artist', ''),
            durationMs=track.get('durationMs', 0), cover=track.get('cover', ''), album=track.get('album', ''),
            volume=session.get('volume', 70), position=round(session.get('position', 0), 1),
            mode=session.get('mode', 'sequence'), error=session.get('error', ''),
            playlistId=session.get('online_playlist_id'), playlistName=session.get('online_playlist_name', ''))

    async def switch_source(self, source):
        if source == self.source: return
        await self.halt(self.state != 'stopped')
        self.sessions[self.source] = {key:getattr(self, key) for key in self.SESSION_FIELDS}
        saved = self.sessions.pop(source, {})
        defaults = dict(track=None, playlist_artist=None, position=0., state='stopped', error='',
            volume=70, mode='sequence', shuffle_queue=[], shuffle_played=set(), history=[],
            history_index=-1, pending_history_index=None, online_queue=[], online_generation=None,
            online_playlist_id=None, online_playlist_name='')
        for key in self.SESSION_FIELDS: setattr(self, key, saved.get(key, defaults[key]))
        self.source = source

    def speech_finished(self, sentence_id):
        if sentence_id and sentence_id == self.awaiting_sentence:
            self.speech_ready.set()

    def playlist(self):
        if self.source == 'netease':
            client = netease(self.conn.config)
            client.current(self.online_generation)
            return [client.tracks.get(value) or dict(id=value, name='网易云歌曲', title='', artist='', source='netease')
                for value in self.online_queue]
        tracks = self.library.tracks()
        if self.playlist_artist:
            tracks = [t for t in tracks if normalize(t['artist']) == normalize(self.playlist_artist)]
        return tracks

    def status(self):
        current = {'state': self.state, 'trackId': self.track['id'] if self.track else None, 'name': self.track['name'] if self.track else '',
                'title': self.track['title'] if self.track else '', 'artist': self.track['artist'] if self.track else '',
                'durationMs':self.track.get('durationMs', 0) if self.track else 0,
                'cover':self.track.get('cover', '') if self.track else '', 'album':self.track.get('album', '') if self.track else '',
                'playlistArtist': self.playlist_artist, 'playlistId':self.online_playlist_id,
                'playlistName':self.online_playlist_name, 'mode': self.mode, 'volume': self.volume, 'position': round(self.position, 1), 'error': self.error}
        return dict(current, source=self.source, players={source:(dict(current) if source==self.source else
            self.saved_status(self.sessions.get(source, {}))) for source in ('local', 'netease')})

    def advance(self, previous=False, use_history=True, automatic=False):
        tracks = self.playlist()
        if not tracks:
            raise ValueError('当前播放列表已没有可用歌曲，请重新选择音乐')
        available = {t['id']: t for t in tracks}
        if automatic and self.mode == 'single' and self.track and self.track['id'] in available:
            self.pending_history_index = None
            return available[self.track['id']]
        direction = -1 if previous else 1
        index = self.history_index + direction
        while use_history and 0 <= index < len(self.history):
            if self.history[index] in available:
                self.pending_history_index = index
                return available[self.history[index]]
            index += direction
        if previous:
            raise ValueError('还没有可播放的上一首')
        self.pending_history_index = None
        if self.mode == 'shuffle':
            self.shuffle_played.intersection_update(available)
            self.shuffle_queue = [i for i in self.shuffle_queue if i in available and i not in self.shuffle_played]
            added = list(set(available) - self.shuffle_played - set(self.shuffle_queue))
            random.shuffle(added)
            self.shuffle_queue.extend(added)
            if not self.shuffle_queue:
                self.shuffle_played.clear()
                self.shuffle_queue = list(available)
                random.shuffle(self.shuffle_queue)
            current = self.track['id'] if self.track else None
            if len(self.shuffle_queue) > 1 and self.shuffle_queue[0] == current:
                self.shuffle_queue[0], self.shuffle_queue[-1] = self.shuffle_queue[-1], self.shuffle_queue[0]
            return available[self.shuffle_queue.pop(0)]
        index = next((i for i, t in enumerate(tracks) if self.track and t['id'] == self.track['id']), -1)
        return tracks[(index + 1) % len(tracks)]

    def record_play(self):
        track_id = self.track['id']
        if self.pending_history_index is not None:
            self.history_index = self.pending_history_index
            self.pending_history_index = None
        elif self.history_index < 0 or self.history[self.history_index] != track_id:
            self.history = self.history[:self.history_index + 1] + [track_id]
            self.history = self.history[-256:]
            self.history_index = len(self.history) - 1
        self.shuffle_played.add(track_id)
        self.shuffle_queue = [i for i in self.shuffle_queue if i != track_id]

    async def halt(self, paused=True, for_chat=False):
        # A cancel can race with a completed PCM read on Python 3.10. Keep an
        # independent stop signal so the audio loop cannot resume after that race.
        self.interrupted_for_chat = for_chat
        self.playback_stop.set()
        task = self.task
        if task and not task.done():
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=5)
            if not done:
                stack = task.get_stack(limit=1)
                where = f'{stack[0].f_code.co_name}:{stack[0].f_lineno}' if stack else 'unknown'
                self.conn.logger.bind(tag=__name__).warning('音乐停止仍在等待，重试取消: source={}, stage={}', self.source, where)
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=5)
                if not done:
                    raise ValueError('音乐暂停尚未完成，请在播放器点击停止后重试')
        if task:
            await asyncio.gather(task, return_exceptions=True)
        if self.task is task:
            self.task = None
        self.state = 'paused' if paused and self.track else 'stopped'
        if not paused:
            self.position = 0

    async def cancel_preparation(self, source=None, invalidate=True):
        pending = self.preparing
        if invalidate and (source is None or source == self.preparing_source):
            self.command_revision += 1
            self.preparing_source = None
        if pending is None or source is not None and pending['source'] != source:
            return
        self.preparing = None
        pending['cancelled'] = True
        self.conn.logger.bind(tag=__name__).info('取消音乐准备: source={}', pending['source'])
        pending['task'].cancel()
        await asyncio.gather(pending['task'], return_exceptions=True)

    async def command(self, action, track_id=None, name=None, volume=None, after_sentence=None, mode=None,
            source=None, artist=None, title=None, playlist_id=None, position=None, playlist_name=None, playlist_kind=None):
        preparing = action in ('play', 'resume', 'next', 'previous', 'seek')
        started_at = time.monotonic()
        if preparing:
            self.command_revision += 1
            revision = self.command_revision
            target_source = ('netease' if playlist_id is not None or playlist_name is not None or playlist_kind is not None
                or str(track_id or '').startswith('netease:') else source or self.source)
            self.preparing_source = target_source
            await self.cancel_preparation(invalidate=False)
            if revision != self.command_revision:
                raise ValueError('播放准备已被新的操作取消')
        elif action in ('pause', 'stop'):
            # Cancel the network lookup before waiting for the player lock.
            await self.cancel_preparation(source)
        operation = asyncio.create_task(self._command(action, track_id=track_id, name=name, volume=volume,
            after_sentence=after_sentence, mode=mode, source=source, artist=artist, title=title,
            playlist_id=playlist_id, position=position, playlist_name=playlist_name, playlist_kind=playlist_kind),
            name='music-control')
        pending = None
        if preparing:
            pending = dict(task=operation, source=target_source, cancelled=False)
            self.preparing = pending
        try:
            # All controls finish before the chat tool's 30-second deadline.
            return await asyncio.wait_for(operation, timeout=25)
        except asyncio.TimeoutError:
            label = '读取网易云歌单' if playlist_name is not None or playlist_kind is not None or playlist_id is not None and not track_id else '音乐操作'
            raise ValueError(label+'超时，请稍后重试或在网页选择') from None
        except asyncio.CancelledError:
            if pending and pending['cancelled']:
                raise ValueError('播放准备已被新的操作取消') from None
            raise
        finally:
            if pending is not None and self.preparing is pending:
                self.preparing = None
                self.preparing_source = None
            if time.monotonic()-started_at >= 3:
                self.conn.logger.bind(tag=__name__).info('音乐操作耗时: action={}, source={}, elapsed_ms={}',
                    action, pending['source'] if pending else source or self.source,
                    round((time.monotonic()-started_at)*1000))

    async def _command(self, action, track_id=None, name=None, volume=None, after_sentence=None, mode=None,
            source=None, artist=None, title=None, playlist_id=None, position=None, playlist_name=None, playlist_kind=None):
        if source not in (None, 'local', 'netease'):
            raise ValueError('请选择本地或网易云播放器')
        if action == 'seek': return await self.seek(position, source or self.source, track_id)
        request_session = getattr(self.conn, 'session_id', None)
        playlist_generation = None
        playlist_request = playlist_name is not None or playlist_kind is not None or playlist_id is not None
        whole_playlist = bool(action=='play' and playlist_request and not track_id)
        if action == 'play' and mode is not None and (not isinstance(mode, str) or mode not in PLAYBACK_MODES):
            raise ValueError('请选择顺序播放、随机播放或单曲循环')
        if playlist_request:
            if action != 'play' or source == 'local': raise ValueError('网易云歌单请使用网易云播放操作')
            if playlist_kind not in (None, 'liked'): raise ValueError('歌单类型无效')
            if playlist_name is not None and not isinstance(playlist_name, str): raise ValueError('歌单名称必须为文字')
            source = 'netease'
            client = netease(self.conn.config)
            client.require_login(); playlist_generation = client.generation
            if playlist_name is not None or playlist_kind == 'liked':
                ordinal = re.fullmatch(r'第?([一二三四五1-5])(?:个)?歌单', playlist_name or '')
                if ordinal and playlist_kind is None:
                    candidates = getattr(self.conn, 'music_playlist_candidates', None)
                    if (not candidates or time.monotonic()-candidates['at']>120
                            or candidates['session'] != request_session):
                        raise ValueError('歌单候选已过期，请重新说歌单名称')
                    client.current(candidates['generation'])
                    if mode is None: mode = candidates.get('mode')
                    index = '一二三四五'.find(ordinal[1]) if not ordinal[1].isdigit() else int(ordinal[1])-1
                    if index >= len(candidates['items']): raise ValueError('没有这个序号的歌单，请选择刚才列出的歌单')
                    selected = candidates['items'][index]
                else:
                    liked = playlist_kind == 'liked' or liked_playlist_name(playlist_name)
                    rows = await client.find_playlists(name=playlist_name, liked=liked)
                    client.current(playlist_generation)
                    if request_session != getattr(self.conn, 'session_id', None): raise ValueError('会话已变化，请重新查询歌单')
                    self.conn.music_playlist_candidates = dict(items=rows[:5], at=time.monotonic(),
                        session=request_session, generation=playlist_generation, mode=mode)
                    if not rows:
                        raise ValueError('没有找到当前账号的红心歌单，请在网页刷新账号歌单' if liked else '没有找到这个账号的歌单，请核对歌单名称')
                    if len(rows)>1:
                        raise ValueError('找到多个歌单：'+'；'.join(f'{i}，{row["name"]}（'+('我创建的' if row['created'] else '我收藏的')+'）' for i,row in enumerate(rows[:5],1))+'。请说播放第几个歌单。')
                    selected = rows[0]
                playlist_id = selected['id']
                name = None; artist = None; title = None
        client = netease(self.conn.config) if action == 'search' or source == 'netease' else None
        def remember(rows, candidate_source):
            if request_session != getattr(self.conn, 'session_id', None): raise ValueError('会话已变化，请重新查询歌曲')
            self.conn.music_candidates = dict(items=[{k:v for k,v in row.items() if k!='_path'} for row in rows[:5]],
                at=time.monotonic(), source=candidate_source, session=request_session,
                generation=netease(self.conn.config).generation if candidate_source=='netease' else None)
        choice = re.fullmatch(r'第?([一二三四五1-5])首(?:歌曲|歌|版本)?', name or '')
        if action == 'play' and choice and not track_id:
            candidates = getattr(self.conn, 'music_candidates', None)
            if not candidates or time.monotonic()-candidates['at'] > 120 or candidates['session'] != getattr(self.conn, 'session_id', None):
                raise ValueError('歌曲候选已过期，请重新说歌名或歌手查询')
            if source and source!=candidates['source']: raise ValueError('歌曲候选来自另一个播放器，请重新查找')
            if candidates['source'] == 'netease': netease(self.conn.config).current(candidates['generation'])
            index = '一二三四五'.find(choice[1]) if not choice[1].isdigit() else int(choice[1])-1
            if index >= len(candidates['items']): raise ValueError('没有这个序号的歌曲，请选择刚才列出的版本')
            track_id = candidates['items'][index]['id']; source = candidates['source']
        if action == 'search':
            if source != 'netease':
                rows = self.library.search(artist, title)
                if rows or source == 'local':
                    rows = recording_choices(rows)
                    remember(rows, 'local')
                    return dict(results=[{k:v for k,v in row.items() if k!='_path'} for row in rows[:5]], hasMore=len(rows)>5, source='local')
            result = await client.search(artist, title)
            rows = recording_choices(result['items'])
            remember(rows, 'netease')
            return dict(results=rows[:5], hasMore=result['hasMore'] or len(rows)>5, source='netease')
        query_play = bool(action == 'play' and not playlist_request and not track_id and (artist or title or name and name!='random'))
        fallback = re.sub(r'^(?:用|在)?网易云(?:音乐)?(?:播放)?', '', name or '').strip()
        conditions = {}
        if artist or title: conditions = {key:value for key,value in dict(artist=artist, title=title).items() if value}
        elif fallback: conditions = song_query(fallback)
        if query_play and source is None and name and name.startswith(('网易云', '用网易云', '在网易云')): source = 'netease'
        if query_play and source != 'netease':
            local_name = conditions['artist']+'的'+(conditions.get('title') or '歌') if conditions.get('artist') else conditions.get('title')
            try: self.library.select(name=local_name)
            except SongNotFound:
                rows = self.library.search(**conditions)
                preferred = original_choice(rows, **conditions)
                if preferred: track_id=preferred['id']; source='local'
                elif len(rows)==1 and not unrequested_variant(rows[0], conditions.get('title')): track_id=rows[0]['id']; source='local'
                elif rows:
                    rows = recording_choices(rows)
                    remember(rows, 'local')
                    raise ValueError('本地有多个匹配：'+'；'.join(f'{i}，{row["name"]}' for i,row in enumerate(rows[:5],1))+'。请说播放第几首。') from None
                elif source == 'local': raise
                else: source = 'netease'
            except ValueError:
                rows = self.library.search(**conditions)
                if rows:
                    preferred = original_choice(rows, **conditions)
                    if preferred: track_id=preferred['id']; source='local'
                    else:
                        rows = recording_choices(rows)
                        remember(rows, 'local')
                        raise ValueError('本地有多个匹配：'+'；'.join(f'{i}，{row["name"]}' for i,row in enumerate(rows[:5],1))+'。请说播放第几首。') from None
                else: raise
            else: name = local_name; source = 'local'
        # Only search the platform when local matching failed or it was explicitly requested.
        if query_play and source == 'netease':
            if self.preparing and self.preparing['task'] is asyncio.current_task():
                self.preparing['source'] = 'netease'
                self.preparing_source = 'netease'
            if not track_id:
                result = await netease(self.conn.config).search(conditions.get('artist'), conditions.get('title'))
                matches = result['items']
                # 的 can also be part of a title (e.g. 最美的太阳). If the inferred
                # artist/title pair has no match, try the complete supplied title.
                if not matches and not artist and not title and conditions.get('artist') and conditions.get('title'):
                    result = await netease(self.conn.config).search(title=fallback)
                    matches = result['items']
                preferred = original_choice(matches, has_more=result['hasMore'], **conditions)
                matches = recording_choices(matches)
                remember(matches, 'netease')
                if not matches: raise ValueError('没有找到符合条件的网易云歌曲，请核对后查询')
                only_unrequested_variant = all(unrequested_variant(row, conditions.get('title')) for row in matches)
                if preferred: track_id=preferred['id']
                elif len(matches) > 1 or result['hasMore'] or only_unrequested_variant:
                    label = '暂未找到普通原曲，查到这些网易云版本：' if only_unrequested_variant else '网易云有多个版本：'
                    raise ValueError(label+'；'.join(f'{i}，{t["name"]}' for i,t in enumerate(matches[:5],1))+'。请说播放第几首，或在网页选择。')
                else: track_id = matches[0]['id']
            source = 'netease'
        if track_id and str(track_id).startswith('netease:'): source = 'netease'
        elif action == 'play' and (track_id or name) and source is None: source = 'local'
        target_source = source or self.source
        if self.preparing and self.preparing['task'] is asyncio.current_task():
            self.preparing['source'] = target_source
            self.preparing_source = target_source
        async with self.lock:
            if self.closing:
                raise ValueError('设备连接已关闭')
            if playlist_request:
                netease(self.conn.config).current(playlist_generation)
                if request_session != getattr(self.conn, 'session_id', None): raise ValueError('会话已变化，请重新选择歌单')
            if target_source != self.source and action in ('mode', 'volume', 'pause', 'stop'):
                saved = self.sessions.setdefault(target_source, {})
                if action == 'volume':
                    if type(volume) is not int or not 0 <= volume <= 100: raise ValueError('音乐音量必须为 0～100 的整数')
                    saved['volume'] = volume
                elif action == 'mode':
                    if mode not in PLAYBACK_MODES: raise ValueError('请选择顺序播放、随机播放或单曲循环')
                    saved['mode'] = mode; saved['shuffle_queue'] = []; saved['shuffle_played'] = set()
                elif action == 'stop': saved['state'] = 'stopped'; saved['position'] = 0
                return self.status()
            # Fetch metadata before switching, so a failed selection keeps the other player intact.
            online_target = None; online_ids = None; online_epoch = None; online_playlist = None
            local_target = None
            if target_source == 'local' and action == 'play' and (track_id or name and name != 'random'):
                local_target = self.library.select(track_id, name)
            if target_source == 'netease' and action in ('play', 'resume', 'next', 'previous'):
                client = netease(self.conn.config)
                client.require_login(); online_epoch = client.generation
                if action == 'play':
                    if playlist_id:
                        online_playlist = await client.playlist(playlist_id)
                        if not online_playlist['ids']: raise ValueError('这个网易云歌单还没有歌曲')
                        if not track_id:
                            playback_mode = mode or (self.mode if target_source==self.source else self.sessions.get(target_source, {}).get('mode', 'sequence'))
                            first = random.choice(online_playlist['ids']) if playback_mode=='shuffle' else online_playlist['ids'][0]
                            track_id = 'netease:'+first
                    if not track_id: raise ValueError('请先在网易云播放器选择歌曲，或提供歌名或歌手查询')
                    online_target = await client.track(track_id)
                    if online_playlist:
                        if song_id(track_id) not in online_playlist['ids']: raise ValueError('歌曲不在所选网易云歌单中，请刷新歌单')
                        online_ids = ['netease:'+value for value in online_playlist['ids']]
                    else: online_ids = [online_target['id']]
                    client.current(online_epoch)
                    if playlist_request and request_session != getattr(self.conn, 'session_id', None): raise ValueError('会话已变化，请重新选择歌单')
            if action in ('play', 'resume', 'next', 'previous'):
                if not shutil.which('ffmpeg'): raise ValueError('服务器缺少 FFmpeg，暂时无法播放')
                saved = self.sessions.get(target_source, {}) if target_source != self.source else None
                if saved is not None and action != 'play' and not saved.get('track'):
                    raise ValueError('此播放器还没有歌曲，请先选择音乐')
                await self.switch_source(target_source)
            if action == 'mode':
                if mode not in PLAYBACK_MODES:
                    raise ValueError('请选择顺序播放、随机播放或单曲循环')
                if mode != self.mode:
                    self.mode = mode
                    self.shuffle_queue = []
                    self.shuffle_played = {self.track['id']} if self.track else set()
                return self.status()  # No cancellation/restart of the current song.
            if action == 'volume':
                if type(volume) is not int or not 0 <= volume <= 100:
                    raise ValueError('音乐音量必须为 0～100 的整数')
                self.volume = volume
                return self.status()
            if action in ('pause', 'stop'):
                await self.halt(action == 'pause')
                return self.status()
            if action not in ('play', 'resume', 'next', 'previous'):
                raise ValueError('不支持的音乐操作')
            if not shutil.which('ffmpeg'):
                raise ValueError('服务器缺少 FFmpeg，暂时无法播放')
            playlist_artist = self.playlist_artist
            navigation = None
            if action == 'resume':
                if not self.track:
                    raise ValueError('没有可继续播放的音乐，请先选择歌曲')
                if self.source == 'netease':
                    netease(self.conn.config).current(self.online_generation)
                    target = await netease(self.conn.config).track(self.track['id'])
                else: target = self.library.select(track_id=self.track['id'])
                position = self.position
            elif action in ('next', 'previous'):
                # Metadata may fail or be cancelled while the old song keeps
                # playing. Consume shuffle/history navigation only on success.
                navigation = copy.copy(self)
                navigation.shuffle_queue = list(self.shuffle_queue)
                navigation.shuffle_played = set(self.shuffle_played)
                target = navigation.advance(action == 'previous')
                if self.source == 'netease': target = await netease(self.conn.config).track(target['id'])
                position = 0
            else:
                if self.source == 'netease':
                    target = online_target
                    if whole_playlist or online_ids != self.online_queue or online_epoch != self.online_generation or mode is not None and mode != self.mode:
                        self.shuffle_queue = []; self.shuffle_played.clear()
                        self.history = []; self.history_index = -1
                    if mode is not None: self.mode = mode
                    self.online_queue = online_ids
                    self.online_generation = online_epoch
                    self.online_playlist_id = str(playlist_id) if online_playlist else None
                    self.online_playlist_name = online_playlist.get('name', '') if online_playlist else ''
                elif not track_id and (name == 'random' or not name and self.mode == 'shuffle'):
                    if self.playlist_artist:
                        self.shuffle_queue = []
                        self.shuffle_played = {self.track['id']} if self.track else set()
                    self.playlist_artist = None
                    self.mode = 'shuffle'
                    target = self.advance(use_history=False)
                else:
                    target = local_target or self.library.select(track_id, name)
                playlist_artist = target.get('_playlist_artist')
                self.pending_history_index = None
                if target.get('_playback_mode'):
                    self.mode = target['_playback_mode']
                if playlist_artist != self.playlist_artist:
                    self.shuffle_queue = []
                    self.shuffle_played.clear()
                position = 0
            await self.halt()
            if navigation is not None:
                self.pending_history_index = navigation.pending_history_index
                # A natural track change may append history during metadata IO.
                if self.pending_history_index is not None and (self.pending_history_index >= len(self.history)
                        or self.history[self.pending_history_index] != target['id']):
                    self.pending_history_index = next((i for i in range(len(self.history)-1, -1, -1)
                        if self.history[i] == target['id']), None)
                self.shuffle_queue = navigation.shuffle_queue
                self.shuffle_played = navigation.shuffle_played
            self.track, self.position, self.error = target, position, ''
            self.playlist_artist = playlist_artist
            self.state = 'loading'
            self.interrupted_for_chat = False
            self.awaiting_sentence = after_sentence
            self.speech_ready = asyncio.Event()
            if not after_sentence:
                self.speech_ready.set()
            self.conn.logger.bind(tag=__name__).info('音乐已排队: action={}, track={}, wait_sentence={}', action, target['name'], after_sentence)
            self.playback_stop = asyncio.Event()
            self.task = asyncio.create_task(self.run(), name='local-music-player')
            return self.status()

    async def close(self):
        self.closing = True
        await self.cancel_preparation()
        async with self.lock:
            if self.task is not asyncio.current_task():
                await self.halt(False)

    async def seek(self, position, source, track_id):
        if type(position) not in (float,int) or not math.isfinite(position) or position<0:
            raise ValueError('播放进度必须为有效的秒数')
        async with self.lock:
            if self.closing: raise ValueError('设备连接已关闭')
            saved = self.sessions.get(source, {}) if source != self.source else None
            track = saved.get('track') if saved is not None else self.track
            if not track or not track_id or track['id']!=track_id: raise ValueError('歌曲已变化，请刷新后调整进度')
            duration = track.get('durationMs', 0)/1000
            if not duration or position>duration: raise ValueError('进度超出歌曲时长，或时长尚未读取')
            position = min(position, max(0, duration-.15))
            if saved is not None:
                saved['position'] = position; saved['state'] = 'paused'; saved['error'] = ''
                return self.status()
            if self.state == 'loading': raise ValueError('歌曲正在准备播放，请稍后调整进度')
            playing = self.state == 'playing'
            await self.halt()
            self.position = position; self.error = ''
            if playing:
                self.state = 'loading'; self.awaiting_sentence = None
                self.speech_ready = asyncio.Event(); self.speech_ready.set()
                self.playback_stop = asyncio.Event()
                self.task = asyncio.create_task(self.run(), name='local-music-player')
            return self.status()

    async def read_frame(self, stream, size):
        # asyncio.wait preserves external cancellation even if the read completes
        # in the same event-loop turn (unlike wait_for on older Python versions).
        # https://github.com/python/cpython/issues/86296
        read = asyncio.create_task(stream.readexactly(size), name='music-pcm-read')
        try:
            done, _ = await asyncio.wait({read}, timeout=10)
            if not done: raise asyncio.TimeoutError()
            return read.result()
        finally:
            if not read.done(): read.cancel()
            await asyncio.gather(read, return_exceptions=True)

    async def run(self):
        conn = self.conn
        stopped = self.playback_stop
        process = None
        lyrics_task = None
        owned = False
        started = False
        music_stream = None
        try:
            # A queue can be empty while the TTS worker is still synthesizing.
            # Voice/web-text tools must wait for their own LAST to finish.
            await asyncio.wait_for(self.speech_ready.wait(), 60)
            if stopped.is_set(): return
            # Voice tools run while chat_lock is held and TTS acknowledgements are queued.
            deadline = time.monotonic() + 30
            while not conn.stop_event.is_set() and not stopped.is_set():
                queues_empty = all(getattr(conn.tts, attr, None) is None or getattr(conn.tts, attr).empty() for attr in ('tts_text_queue', 'tts_audio_queue'))
                if (not conn.client_is_speaking and not getattr(conn, 'tts_finishing', False)
                        and queues_empty and conn.chat_lock.acquire(blocking=False)):
                    owned = True
                    break
                if time.monotonic() > deadline:
                    raise ValueError('设备仍在回复，请稍后重新播放')
                await asyncio.sleep(.1)
            if not owned:
                self.state = 'stopped'
                return
            import opuslib_next as opus
            from core.handle.sendAudioHandle import sendAudio, send_tts_message, _wait_for_audio_completion
            from core.conversation.standby import begin_interaction
            rate = conn.sample_rate
            frame_size = int(rate * .06)
            encoder = opus.Encoder(rate, 1, opus.APPLICATION_AUDIO)
            while not conn.stop_event.is_set() and not stopped.is_set():
                self.state = 'loading'
                inputs = []
                timeline = LyricsTimeline()
                if self.source == 'netease':
                    client = netease(conn.config)
                    client.current(self.online_generation)
                    self.track = await client.track(self.track['id'])
                    if stopped.is_set(): break
                    # Fetch in parallel, never await the lyric provider in the PCM loop.
                    lyrics_task = asyncio.create_task(client.lyrics(self.track['id']), name='music-lyrics')
                    audio = await client.play_url(self.track['id'])
                    client.current(self.online_generation)
                    inputs = ['-protocol_whitelist', 'http,https,tcp,tls,crypto', '-rw_timeout', '15000000']
                else:
                    meta = await local_details(self.track)
                    timeline = LyricsTimeline(meta)
                    self.track = dict(self.track, **{key:value for key,value in meta.items() if key in ('durationMs','cover','album')})
                    audio = str(self.track['_path'])
                if stopped.is_set(): break
                process = await asyncio.create_subprocess_exec('ffmpeg', '-nostdin', '-v', 'error', *inputs, '-ss', str(self.position), '-i', audio, '-vn', '-f', 's16le', '-ar', str(rate), '-ac', '1', 'pipe:1', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                if stopped.is_set(): break
                begin_interaction(conn)
                conn.sentence_id = str(uuid.uuid4())
                music_stream = conn.sentence_id
                self.stream_id = music_stream
                conn.client_abort = False
                conn.client_is_speaking = True
                started = True
                await send_tts_message(conn, 'start', media='music', music_title=self.track['name'])
                last_caption = None

                async def display_lyric():
                    nonlocal lyrics_task, timeline, last_caption
                    if lyrics_task and lyrics_task.done():
                        finished, lyrics_task = lyrics_task, None
                        if not finished.cancelled():
                            try:
                                timeline = LyricsTimeline(finished.result())
                            except Exception as exc:
                                conn.logger.bind(tag=__name__).warning('音乐歌词获取失败，保留歌名: {}', type(exc).__name__)
                    # The song title occupies its own LCD row, above the lyrics.
                    caption = timeline.text_at(self.position) or (
                        '间奏' if timeline.lines else '歌词加载中…' if lyrics_task else '暂无同步歌词')
                    # Repeated frames must not restart a long line's LCD scrolling.
                    if caption != last_caption:
                        await send_tts_message(conn, 'sentence_start', caption, media='music', stream_id=music_stream)
                        last_caption = caption

                await display_lyric()
                self.state = 'loading'
                sent = False
                while not conn.stop_event.is_set() and not conn.client_abort and not stopped.is_set():
                    if self.source == 'netease': netease(conn.config).current(self.online_generation)
                    try:
                        pcm = await self.read_frame(process.stdout, frame_size * 2)
                    except asyncio.IncompleteReadError as exc:
                        pcm = exc.partial
                    if stopped.is_set() or not pcm:
                        break
                    samples = len(pcm) // 2
                    await display_lyric()
                    if stopped.is_set(): break
                    # Scale only music PCM; speech and device master volume stay independent.
                    if self.volume != 100:
                        values = array('h', pcm)
                        if sys.byteorder != 'little':
                            values.byteswap()
                        gain = self.volume / 100
                        values = array('h', (int(value * gain) for value in values))
                        if sys.byteorder != 'little':
                            values.byteswap()
                        pcm = values.tobytes()
                    packet = encoder.encode(pcm.ljust(frame_size * 2, b'\0'), frame_size)
                    await sendAudio(conn, packet)
                    if not sent:
                        self.record_play()
                        self.state = 'playing'
                        conn.logger.bind(tag=__name__).info('音乐开始播放: source={}, track={}, volume={}', self.source, self.track['name'], self.volume)
                    await asyncio.wait_for(conn.audio_rate_controller.queue_empty_event.wait(), 10)
                    if stopped.is_set(): break
                    self.position += samples / rate
                    sent = True
                    conn.last_activity_time = time.time() * 1000
                if conn.client_abort or stopped.is_set():
                    self.state = 'paused'
                    break
                else:
                    await asyncio.wait_for(process.wait(), 5)
                    if stopped.is_set(): break
                    if process.returncode or not sent:
                        raise ValueError('网易云音源读取失败，请检查网络或重新播放' if self.source == 'netease' else 'MP3 解码失败，请检查音乐文件')
                    await asyncio.wait_for(_wait_for_audio_completion(conn), 10)
                    if stopped.is_set(): break
                    tracks = self.playlist()
                    if not tracks:
                        self.state = 'stopped'
                        self.position = 0
                        break
                    self.track = self.advance(automatic=True)
                    self.position = 0
                    process = None
                    if lyrics_task:
                        lyrics_task.cancel()
                        await asyncio.gather(lyrics_task, return_exceptions=True)
                        lyrics_task = None
        except asyncio.CancelledError:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            self.state = 'error'
            self.error = '音源读取超时，请检查网络后重新播放' if process else '等待语音回复结束超时，请重新播放'
            conn.logger.bind(tag=__name__).warning('音乐播放等待超时: source={}, sentence={}', self.source, self.awaiting_sentence)
        except Exception as exc:
            self.state = 'error'
            self.error = str(exc) if isinstance(exc, ValueError) else '音乐播放失败，请检查音频依赖和设备连接'
            conn.logger.bind(tag=__name__).warning('音乐播放失败: {}', type(exc).__name__)
        finally:
            try:
                if lyrics_task:
                    lyrics_task.cancel()
                    await asyncio.gather(lyrics_task, return_exceptions=True)
                if process:
                    if process.returncode is None:
                        try: process.kill()
                        except ProcessLookupError: pass
                    try:
                        await asyncio.wait_for(process.communicate(), 5)
                    except (OSError, asyncio.TimeoutError):
                        conn.logger.bind(tag=__name__).warning('音乐解码进程清理未完成，释放播放占用')
            finally:
                # Cleanup errors or a second cancellation must never strand chat_lock.
                try:
                    if started and music_stream == conn.sentence_id:
                        conn.client_is_speaking = False
                        controller = getattr(conn, 'audio_rate_controller', None)
                        if controller: controller.reset()
                        try:
                            from core.handle.sendAudioHandle import send_tts_message
                            from core.conversation.standby import enter_standby, supports_standby, conversation_awake
                            if not self.closing:
                                await asyncio.wait_for(send_tts_message(conn, 'stop', media='music_interrupted' if self.interrupted_for_chat else 'music'), 2)
                                if (not self.interrupted_for_chat and supports_standby(conn)
                                        and not conversation_awake(conn)):
                                    await asyncio.wait_for(enter_standby(conn), 2)
                        except Exception:
                            pass
                finally:
                    self.stream_id = None
                    if owned:
                        conn.chat_lock.release()

def player(conn):
    if not getattr(conn, 'local_music', None):
        conn.local_music = MusicPlayer(conn)
    return conn.local_music

async def pause_for_chat(conn, for_chat=True):
    current = getattr(conn, 'local_music', None)
    if current:
        await current.cancel_preparation()
    if current and current.state in ('playing', 'loading'):
        async with current.lock:
            await current.halt(for_chat=for_chat)
        if for_chat:
            conn.return_to_standby = False

async def prompt_music_control(conn):
    """Ask for a playback action without treating the prompt as user input."""
    from core.handle.sendAudioHandle import send_tts_message
    from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
    from core.conversation.standby import begin_interaction
    begin_interaction(conn, awake=True)
    conn.return_to_standby = False
    conn.client_abort = False
    conn.sentence_id = str(uuid.uuid4())
    conn.music_wake_prompt_sentence = conn.sentence_id
    conn.task_followup_sentence = conn.sentence_id
    conn.music_wake_prompt_until = time.monotonic() + 5
    conn.client_is_speaking = True
    conn.logger.bind(tag=__name__).info('音乐唤醒：已暂停歌曲，询问播放操作')
    await send_tts_message(conn, 'start')
    for kind, content in ((SentenceType.FIRST, None),
                          (SentenceType.MIDDLE, '想暂停、继续播放，还是切换上一首、下一首？也可以调整音乐音量。'),
                          (SentenceType.LAST, None)):
        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=kind,
            content_type=ContentType.TEXT if content else ContentType.ACTION, content_detail=content))
