"""原创短提示旋律，按短段发送；网页状态与唤醒共同终止同一播放任务。"""
import asyncio
import json
import math
import struct
import time
import shutil
import sys
from array import array
from functools import lru_cache
from core.reminders import client as reminder_client
from core.handle.sendAudioHandle import sendAudio
from core.conversation.standby import enter_standby, conversation_awake

@lru_cache(maxsize=8)
def melody_packets(volume=30):
    if type(volume) is not int or not 0<=volume<=100: raise ValueError('音乐音量无效')
    if volume==0: return []
    if volume>0:
        from core.utils.util import pcm_to_data_stream
        # 轻柔五声音阶，无外部歌曲依赖。生成8秒、24kHz单声道PCM。
        notes=(261.63,329.63,392.00,440.00,392.00,329.63,293.66,261.63)
        pcm=bytearray()
        for note in notes:
            for i in range(24000):
                t=i/24000
                envelope=min(1,t/0.03)*math.exp(-4*t)
                value=int(8000*(volume/100)*envelope*(math.sin(2*math.pi*note*t)+0.2*math.sin(4*math.pi*note*t)))
                pcm.extend(struct.pack('<h',value))
        packets=[]
        pcm_to_data_stream(bytes(pcm),True,packets.append,sample_rate=24000)
        return packets


class MusicWait:
    def __init__(self,conn,device,reminder_id,attempt,*,members=None):
        self.conn=conn; self.device=device; self.id=reminder_id; self.attempt=attempt
        self.members=list(dict.fromkeys(members or [(reminder_id,attempt)]))
        self.interrupted=False; self.task=None
        self.released=False

    async def play_track(self, track_id, volume):
        """Stream one selected track, repeat only inside this reminder's lease."""
        from core.music import MusicLibrary
        import opuslib_next as opus
        if not shutil.which('ffmpeg'):
            raise ValueError('服务器缺少 FFmpeg')
        track=MusicLibrary(self.conn.config).select(track_id=track_id)
        rate=self.conn.sample_rate
        samples=int(rate*.06)
        encoder=opus.Encoder(rate,1,opus.APPLICATION_AUDIO)
        while not self.conn.stop_event.is_set() and not self.conn.client_abort:
            process=await asyncio.create_subprocess_exec('ffmpeg','-nostdin','-v','error','-i',str(track['_path']),
                '-vn','-f','s16le','-ar',str(rate),'-ac','1','pipe:1',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            sent=False
            try:
                while not self.conn.stop_event.is_set() and not self.conn.client_abort:
                    try: pcm=await asyncio.wait_for(process.stdout.readexactly(samples*2),5)
                    except asyncio.IncompleteReadError as exc: pcm=exc.partial
                    if not pcm: break
                    values=array('h',pcm)
                    if sys.byteorder!='little': values.byteswap()
                    values=array('h',(int(v*volume()/100) for v in values))
                    if sys.byteorder!='little': values.byteswap()
                    packet=encoder.encode(values.tobytes().ljust(samples*2,b'\0'),samples)
                    await sendAudio(self.conn,packet)
                    await self.conn.audio_rate_controller.queue_empty_event.wait()
                    sent=True
                await asyncio.wait_for(process.wait(),5)
                if process.returncode or not sent: raise ValueError('提醒歌曲无法解码')
            finally:
                if process.returncode is None:
                    try: process.kill()
                    except ProcessLookupError: pass
                await process.communicate()

    def release(self):
        if not self.released:
            self.released=True
            self.conn.chat_lock.release()

    async def run(self):
        conn=self.conn
        started=time.monotonic()
        player=None
        confirmed=False
        ended_without_confirmation=False
        try:
            packets=[]
            volume=None
            self.conn.reminder_music_warning=''
            async def play():
                index=0
                while not conn.client_abort and not conn.stop_event.is_set():
                    if not packets:
                        await asyncio.sleep(0.1)
                        continue
                    index%=len(packets)
                    chunk=(packets+packets)[index:index+15]
                    index=(index+15)%len(packets)
                    await sendAudio(conn,chunk)
                    # 等发送队列即可，不在每个短段重复等待设备预缓冲耗尽。
                    await conn.audio_rate_controller.queue_empty_event.wait()
            async def selected_play(track_id):
                if track_id:
                    try:
                        await self.play_track(track_id,lambda: volume)
                        return
                    except asyncio.CancelledError: raise
                    except Exception:
                        conn.reminder_music_warning='提醒选曲已失效或无法读取，本次使用默认提醒音乐。'
                        conn.logger.bind(tag=__name__).warning(conn.reminder_music_warning)
                await play()
            while time.monotonic()-started<65 and not conn.stop_event.is_set() and not conn.client_abort:
                snapshot=await reminder_client.state(self.device)
                indexed={x['id']:x for x in snapshot['items']}
                pending=[]; dispatching=False
                for reminder_id,attempt in self.members:
                    item=indexed.get(reminder_id)
                    if not item:
                        ended_without_confirmation=True; continue
                    if item['status']=='completed':
                        confirmed=True; continue
                    if item.get('attemptId')!=attempt:
                        ended_without_confirmation=True; continue
                    if item['status']=='dispatching' and time.monotonic()-started<10:
                        pending.append((reminder_id,attempt)); dispatching=True
                    elif item['status']=='awaiting_confirmation' and item.get('ackDeadline',0)>snapshot['serverNow']:
                        pending.append((reminder_id,attempt))
                    else:
                        ended_without_confirmation=True
                self.members=pending
                if not pending: break
                if dispatching:
                    await asyncio.sleep(0.2); continue
                requested_volume=snapshot.get('musicVolume',30)
                if requested_volume!=volume:
                    packets=await asyncio.to_thread(melody_packets,requested_volume)
                    volume=requested_volume
                if player is None:
                    from core.handle.sendAudioHandle import send_tts_message
                    from core.device.face import send_face
                    await send_tts_message(conn,'reminder_wait',media='reminder',stream_id=self.attempt)
                    if conn.client_abort or conn.stop_event.is_set() or conn.sentence_id!=self.attempt: break
                    await send_face(conn,'singing')
                    if conn.client_abort or conn.stop_event.is_set() or conn.sentence_id!=self.attempt: break
                    flow=getattr(conn,'audio_flow_control',{})
                    if flow.get('sentence_id')==self.attempt:
                        flow['expected_gap']='reminder_music_start'
                    player=asyncio.create_task(selected_play(snapshot.get('musicTrackId')))
                if player.done():
                    player.result(); break
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            conn.logger.bind(tag=__name__).warning("提醒音乐停止: {}",type(exc).__name__)
        finally:
            if player:
                player.cancel()
                await asyncio.gather(player,return_exceptions=True)
            if conn.sentence_id==self.attempt:
                controller=getattr(conn,'audio_rate_controller',None)
                if controller: controller.reset()
                conn.client_is_speaking=False
                try:
                    handoff=(getattr(self,'handoff_generation',None)==getattr(conn,'input_generation',0)
                             and not conn.client_abort and not conversation_awake(conn))
                    message={'type':'tts','state':'stop','session_id':conn.session_id,'stream_id':self.attempt}
                    if handoff: message['listen_after']=False
                    await asyncio.wait_for(conn.websocket.send(json.dumps(message)),2)
                    if handoff:
                        conn.standby=True
                        conn.return_to_standby=True
                    if not self.interrupted:
                        await asyncio.wait_for(enter_standby(conn),2)
                        if confirmed and not ended_without_confirmation and not self.members:
                            from core.device.face import send_face
                            await send_face(conn,'reminder_ack')
                except Exception: pass
            self.release()
            if getattr(conn,'reminder_music',None) is self: conn.reminder_music=None


async def handoff_music(conn):
    """Yield reminder melody to a newly due notice, without extending any ACK deadline."""
    music=getattr(conn,'reminder_music',None)
    if not music: return []
    generation=getattr(conn,'input_generation',0)
    session=conn.session_id
    if (music.interrupted or conn.client_abort or conn.stop_event.is_set()
            or conn.sentence_id!=music.attempt or getattr(conn,'client_have_voice',False)
            or getattr(conn,'tts_finishing',False) or conversation_awake(conn)):
        return None
    members=list(music.members)
    music.handoff_generation=generation
    music.interrupted=True  # Cleanup stops only this melody; it does not enter standby.
    if music.task:
        music.task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(music.task),6)
        except asyncio.CancelledError:
            # A task cancelled before its first step has not run its finally block.
            if not music.task.done(): raise
        except TimeoutError:
            return None  # Its own cleanup still owns the lock; do not force-release it.
        except Exception as exc:
            conn.logger.bind(tag=__name__).warning('提醒音乐交接未完成: {}',type(exc).__name__)
            return None
    music.release()
    if getattr(conn,'reminder_music',None) is music: conn.reminder_music=None
    if (getattr(conn,'input_generation',0)!=generation or conn.session_id!=session
            or conn.sentence_id!=music.attempt or conn.client_abort or conn.stop_event.is_set()
            or getattr(conn,'client_have_voice',False) or conversation_awake(conn)):
        return None  # A user's wake/speech always wins over another reminder.
    controller=getattr(conn,'audio_rate_controller',None)
    if controller: controller.reset()
    conn.client_is_speaking=False
    conn.standby=True  # Internal audio handoff only; no idle command or microphone change.
    conn.return_to_standby=True
    return members


async def interrupt_music(conn):
    music=getattr(conn,'reminder_music',None)
    if not music: return
    music.interrupted=True
    conn.reminder_response_until=time.monotonic()+90
    if music.task:
        music.task.cancel()
        await asyncio.gather(music.task,return_exceptions=True)
    # 任务在第一次执行前就被取消时，协程 finally 尚未进入。
    music.release()
    if getattr(conn,'reminder_music',None) is music: conn.reminder_music=None
    conn.return_to_standby=False
    async def listening(reminder_id,attempt):
        try:
            await reminder_client.command(music.device,{'action':'listening','id':reminder_id,'attemptId':attempt})
        except Exception: pass
    await asyncio.gather(*(listening(reminder_id,attempt) for reminder_id,attempt in music.members))
