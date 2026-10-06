"""Dashboard reads and allowlisted controls, with MCP acknowledgement and read-back."""
import asyncio
import hmac
import ipaddress
import json
import os
import time
from aiohttp import web
from core.api.volume_handler import VolumeHandler

EXPRESSIONS={'neutral','happy','laughing','loving','angry','sad','crying','tired','sleepy','confused','curious','surprised','shy','confident','winking','peek_left','peek_right','daydream','yawn'}
TOOLS={'volume':'self.audio_speaker.set_volume','brightness':'self.screen.set_brightness','expression':'self.face.set_expression','colors':'self.face.set_colors'}

class DevicePanelHandler(VolumeHandler):
    def __init__(self, ws_server):
        super().__init__(ws_server)
        self.locks={}
        self.cache={}

    def connected(self, device, conn):
        return (self.ws_server and self.ws_server.device_handlers.get(device) is conn
                and not conn.stop_event.is_set())

    def authorized(self, request):
        key=os.environ.get('XIAOZHI_DEVICE_SERVICE_KEY','')
        if not key:
            try:
                ip=ipaddress.ip_address(request.remote or '')
                if not (ip.is_loopback or getattr(ip,'ipv4_mapped',None) and ip.ipv4_mapped.is_loopback): return False
            except ValueError: return False
        return hmac.compare_digest(request.headers.get('Service-Key',''),key or 'xiaozhi-device')

    def supported(self, conn):
        client=getattr(conn,'mcp_client',None)
        if not client: return set()
        return {client.name_mapping.get(name,name) for name in client.tools}

    async def snapshot(self, device, conn, fresh=False):
        if not self.connected(device, conn):
            self.cache.pop(device, None)
            raise ValueError('设备连接已变化，请刷新状态')
        cached=self.cache.get(device)
        if not fresh and cached and cached[0] is conn and time.monotonic()-cached[1]<.75:
            return cached[2]
        tools=self.supported(conn)
        if 'self.dashboard.get_state' in tools:
            actual=json.loads(await self._call(conn,'self.dashboard.get_state',{}))
        else:
            actual={'hardware':json.loads(await self._call(conn,'self.get_device_status',{})),'face':None,'deviceState':None}
        music=getattr(conn,'local_music',None)
        if not self.connected(device, conn):
            self.cache.pop(device, None)
            raise ValueError('设备连接已变化，请刷新状态')
        data={**actual,'deviceId':device,'online':True,'observedAt':int(time.time()*1000),
                'configState':getattr(conn,'runtime_config_status','applied'),
                'controls':{action:tool in tools and (action not in ('colors','expression') or actual.get('face') is not None) for action,tool in TOOLS.items()},
                'music':music.status() if music else None}
        self.cache[device]=(conn,time.monotonic(),data)
        return data

    async def handle(self, request):
        if not self.authorized(request): return web.json_response({'code':401,'msg':'设备服务认证失败'},status=401)
        try:
            devices={key:conn for key,conn in (self.ws_server.device_handlers if self.ws_server else {}).items() if not conn.stop_event.is_set()}
            for key in list(self.cache):
                if devices.get(key) is not self.cache[key][0]: self.cache.pop(key,None)
            for key,lock in list(self.locks.items()):
                if key not in devices and not lock.locked(): self.locks.pop(key,None)
            if request.method=='GET':
                device=request.query.get('deviceId')
                choices=list(devices)
                if not device and len(choices)==1: device=choices[0]
                if not device or device not in devices:
                    return web.json_response({'code':0,'data':{'devices':choices,'selected':None}})
                async with self.locks.setdefault(device,asyncio.Lock()):
                    data=await self.snapshot(device,devices[device])
                return web.json_response({'code':0,'data':{'devices':choices,'selected':data}})
            body=await request.json()
            if not isinstance(body,dict): raise ValueError('请求格式错误')
            device=body.get('deviceId'); action=body.get('action')
            if not isinstance(device,str) or not isinstance(action,str): raise ValueError('设备和操作名称必须为字符串')
            conn=devices.get(device)
            if not conn: raise ValueError('设备已离线，请刷新状态')
            if action not in TOOLS: raise ValueError('不支持的控制操作')
            if action in ('volume','brightness'):
                value=body.get('value')
                if type(value) is not int or not 0<=value<=100: raise ValueError('数值必须为0至100的整数')
                args={action:value}
            elif action=='expression':
                if not isinstance(body.get('value'),str) or body['value'] not in EXPRESSIONS: raise ValueError('请选择支持的表情')
                current=getattr(conn,'local_music',None)
                if current and current.state in ('playing','loading'): raise ValueError('播放音乐时保留唱歌表情，请暂停音乐后再预览')
                args={'emotion':body['value']}
            else:
                args={key:body.get(key) for key in ('eye_color','mouth_color') if key in body}
                if not args or any(type(v) is not int or not 0<=v<=0xffffff for v in args.values()): raise ValueError('颜色值无效')
            async with self.locks.setdefault(device,asyncio.Lock()):
                self.cache.pop(device,None)
                if not self.connected(device,conn): raise ValueError('设备连接已变化，请刷新状态')
                if TOOLS[action] not in self.supported(conn): raise ValueError('设备不支持此操作，请更新固件')
                ack=json.loads(await self._call(conn,TOOLS[action],args))
                if ack is not True: raise ValueError('设备拒绝或未执行此操作')
                if action=='brightness': await asyncio.sleep(0.6)  # Firmware fades up to 100 steps at 5ms.
                data=await self.snapshot(device,conn,fresh=True)
                hardware=data.get('hardware') or {}; face=data.get('face') or {}
                verified=(hardware.get('audio_speaker',{}).get('volume')==args['volume'] if action=='volume' else
                          hardware.get('screen',{}).get('brightness')==args['brightness'] if action=='brightness' else
                          face.get('expression')==args['emotion'] if action=='expression' else
                          all(face.get('eyeColor' if k=='eye_color' else 'mouthColor')==v for k,v in args.items()))
                if not verified: raise ValueError('设备已回应，但当前状态与设置不一致，请刷新核实')
            return web.json_response({'code':0,'data':{'confirmation':'verified','selected':data},'msg':'设备已执行，实际状态已核对'})
        except ValueError as exc:
            return web.json_response({'code':400,'msg':str(exc)},status=400)
        except Exception:
            # A timeout is unknown, not proof of failure and never an automatic retry.
            return web.json_response({'code':503,'msg':'未获得完整设备确认，执行结果未知，请刷新核实'},status=503)
