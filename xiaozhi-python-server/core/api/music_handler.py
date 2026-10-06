import asyncio
import hmac
import ipaddress
import os
import uuid
from pathlib import Path
from aiohttp import web
from core.music import MusicLibrary, MAX_BYTES, player, validate_mp3, local_details


class MusicHandler:
    def __init__(self, config, ws_server):
        self.library = MusicLibrary(config)
        self.ws_server = ws_server
        self.mutation_lock = asyncio.Lock()

    def devices(self):
        return self.ws_server.device_handlers if self.ws_server else {}

    def playback_devices(self):
        return [{'id':key, 'name':getattr(conn, 'device_name', None),
                 'reminderMusicWarning':getattr(conn, 'reminder_music_warning', ''),
                 'status':player(conn).status()} for key,conn in list(self.devices().items())
                if not conn.stop_event.is_set()]

    async def handle_status(self, request):
        if not self.authorized(request):
            return web.json_response({'code':401, 'msg':'音乐服务认证失败'}, status=401)
        # No directory traversal or metadata reads on the polling path.
        return web.json_response({'code':0, 'data':{'devices':self.playback_devices()}})

    @staticmethod
    def authorized(request):
        key = os.environ.get('XIAOZHI_MUSIC_SERVICE_KEY', '')
        if not key:
            try:
                ip = ipaddress.ip_address(request.remote or '')
                if not (ip.is_loopback or getattr(ip, 'ipv4_mapped', None) and ip.ipv4_mapped.is_loopback):
                    return False
            except ValueError:
                return False
        return hmac.compare_digest(request.headers.get('Service-Key', ''), key or 'xiaozhi-music')

    async def handle(self, request):
        if not self.authorized(request):
            return web.json_response({'code':401, 'msg':'音乐服务认证失败'}, status=401)
        try:
            if request.method == 'GET':
                if request.match_info.get('track_id'):
                    track = self.library.select(track_id=request.match_info['track_id'])
                    details = await local_details(track)
                    return web.json_response(dict(code=0, data={key:details.get(key, '') for key in ('lyric','translation','instrumental','durationMs','album','cover')}))
                library_tracks = await asyncio.to_thread(self.library.tracks)
                tracks = [{k:v for k,v in t.items() if k != '_path'} for t in library_tracks]
                devices = self.playback_devices()
                return web.json_response({'code':0, 'data':{'tracks':tracks, 'devices':devices}})
            if request.method == 'PUT':
                async with self.mutation_lock:
                    return await self.upload(request)
            if request.method == 'DELETE':
                async with self.mutation_lock:
                    track = self.library.select(track_id=request.match_info['track_id'])
                    for conn in list(self.devices().values()):
                        current = getattr(conn, 'local_music', None)
                        if current:
                            async with current.lock:
                                saved = current.sessions.get('local', {})
                                if (saved.get('track') or {}).get('id') == track['id']:
                                    saved['track'] = None; saved['state'] = 'stopped'; saved['position'] = 0
                                if current.track and current.track['id'] == track['id']:
                                    await current.halt(False)
                                    current.track = None
                    track['_path'].unlink()
                    track['_path'].with_suffix('.mp3.json').unlink(missing_ok=True)
                return web.json_response({'code':0})
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('音乐请求格式错误')
            conn = self.devices().get(body.get('deviceId'))
            if not conn or conn.stop_event.is_set() or not conn.tts:
                raise ValueError('请选择一台已就绪的在线设备')
            if any(not isinstance(body.get(k, ''), str) for k in ('action','trackId','deviceId')):
                raise ValueError('音乐请求参数无效')
            data = await player(conn).command(body.get('action'), body.get('trackId'), volume=body.get('volume'), mode=body.get('mode'), source=body.get('source'), position=body.get('position'))
            return web.json_response({'code':0, 'data':data})
        except ValueError as exc:
            return web.json_response({'code':400, 'msg':str(exc)}, status=400)
        except (OSError, KeyError, TypeError):
            return web.json_response({'code':503, 'msg':'音乐文件操作失败，请刷新后重试'}, status=503)

    async def upload(self, request):
        name = request.query.get('name', '').strip()
        if not name or len(name)>180 or any(c in name for c in '/\\<>:"|?*') or any(ord(c)<32 for c in name) or Path(name).suffix.lower()!='.mp3':
            raise ValueError('请选择文件名有效的 MP3 文件')
        name = Path(name).stem + '.mp3'
        target = self.library.root / name
        if target.exists():
            raise ValueError('同名音乐已存在，请改名后上传')
        if request.content_length and request.content_length > MAX_BYTES:
            raise ValueError('单个 MP3 最大 32 MB')
        temp = self.library.root / ('.upload-' + uuid.uuid4().hex)
        try:
            total = 0
            with temp.open('xb') as output:
                async for chunk in request.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError('单个 MP3 最大 32 MB')
                    output.write(chunk)
            await validate_mp3(temp)
            temp.rename(target)
            await local_details(dict(_path=target))
            return web.json_response({'code':0, 'msg':'上传成功'})
        finally:
            temp.unlink(missing_ok=True)
