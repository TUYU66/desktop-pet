import asyncio
import re
from aiohttp import web
from core.api.music_handler import MusicHandler
from core.music import player
from core.music.netease import netease
from core.music.download import import_song


class NeteaseHandler:
    def __init__(self, config, ws_server, music_handler=None):
        self.client = netease(config)
        self.ws_server = ws_server
        self.account_lock = asyncio.Lock()
        self.download_lock = asyncio.Lock()
        self.music_handler = music_handler or MusicHandler(config, ws_server)

    def devices(self):
        return self.ws_server.device_handlers if self.ws_server else {}

    async def reset_players(self):
        for conn in list(self.devices().values()):
            current = getattr(conn, 'local_music', None)
            if current:
                await current.cancel_preparation('netease')
                async with current.lock:
                    current.sessions.pop('netease', None)
                    if current.source == 'netease':
                        await current.halt(False)
                        current.track = None
                        current.online_queue = []
                        current.error = ''

    @staticmethod
    def offset(request):
        value = request.query.get('offset', '0')
        if not re.fullmatch(r'\d{1,6}', value): raise ValueError('分页参数无效')
        return int(value)

    async def handle(self, request):
        if not MusicHandler.authorized(request):
            return web.json_response(dict(code=401, msg='音乐服务认证失败'), status=401)
        operation = request.match_info.get('operation', '')
        try:
            if operation == 'account':
                async with self.account_lock:
                    if request.method == 'DELETE':
                        self.client.forget()
                        await self.reset_players()
                    elif request.method != 'GET': raise ValueError('不支持的账号操作')
                    data = await self.client.account()
                    if not data['loggedIn']: await self.reset_players()
            elif operation == 'qr' and request.method == 'POST':
                async with self.account_lock: data = await self.client.create_qr()
            elif operation == 'qr' and request.method == 'GET':
                token = request.query.get('token', '')
                if not re.fullmatch(r'[a-f0-9]{32}', token): raise ValueError('扫码请求编号无效')
                async with self.account_lock:
                    data = await self.client.check_qr(token)
                    if data['state'] == 'authorized': await self.reset_players()
            elif operation == 'playlists' and request.method == 'GET':
                data = await self.client.playlists(self.offset(request))
            elif operation == 'songs' and request.method == 'GET':
                data = await self.client.playlist_songs(request.query.get('playlistId', ''), self.offset(request), query=request.query.get('query', ''))
            elif operation == 'search' and request.method == 'GET':
                data = await self.client.search(request.query.get('artist'), request.query.get('title'), self.offset(request))
            elif operation == 'lyrics' and request.method == 'GET':
                data = await self.client.lyrics(request.query.get('trackId', ''))
            elif operation == 'download' and request.method == 'POST':
                body = await request.json()
                if not isinstance(body, dict) or not isinstance(body.get('trackId'), str): raise ValueError('歌曲编号无效')
                async with self.download_lock:
                    data = await import_song(self.client, self.music_handler.library, body['trackId'], self.music_handler.mutation_lock)
            elif operation == 'play' and request.method == 'POST':
                body = await request.json()
                if not isinstance(body, dict) or not isinstance(body.get('deviceId'), str) or not isinstance(body.get('trackId'), str):
                    raise ValueError('播放请求参数无效')
                conn = self.devices().get(body['deviceId'])
                if not conn or conn.stop_event.is_set() or not conn.tts: raise ValueError('桌面宠物未连接或尚未就绪')
                data = await player(conn).command('play', track_id=body['trackId'], source='netease', playlist_id=body.get('playlistId'))
            else:
                return web.json_response(dict(code=404, msg='网易云接口不存在'), status=404)
            return web.json_response(dict(code=0, data=data))
        except ValueError as exc:
            return web.json_response(dict(code=400, msg=str(exc)), status=400)
        except (OSError, KeyError, TypeError, AttributeError):
            return web.json_response(dict(code=503, msg='网易云服务暂时不可用，请稍后重试'), status=503)
