"""保存好的提醒直接TTS，不经过聊天模型、不伪造用户消息或长期记忆。"""
import asyncio
import hmac
import ipaddress
import os
import time
import uuid
from aiohttp import web
from core.handle.sendAudioHandle import sendAudioMessage, send_tts_message, _wait_for_audio_completion
from core.conversation.standby import begin_interaction, enter_standby
from core.providers.tts.dto.dto import SentenceType


class ReminderHandler:
    def __init__(self, ws_server):
        self.ws_server=ws_server
        self.attempts={}
        self.batch_attempts={}

    @staticmethod
    def validate_item(body):
        if not isinstance(body,dict): raise ValueError()
        attempt,text=(body.get(k) for k in ('attemptId','text'))
        if not all(isinstance(v,str) and v.strip() for v in (attempt,text)) or len(text)>120: raise ValueError()
        uuid.UUID(attempt)
        reminder_id=body.get('reminderId')
        if reminder_id is not None:
            if not isinstance(reminder_id,str): raise ValueError()
            uuid.UUID(reminder_id)
        number=body.get('deliveryNumber',1)
        if type(number) is not int or not 1<=number<=3: raise ValueError()
        return dict(attemptId=attempt,text=text,reminderId=reminder_id,deliveryNumber=number)

    @staticmethod
    def authorized(request):
        configured=os.environ.get("XIAOZHI_REMINDER_SERVICE_KEY","")
        if not configured:
            try:
                address=ipaddress.ip_address(request.remote or "")
                if not (address.is_loopback or getattr(address,"ipv4_mapped",None) and address.ipv4_mapped.is_loopback): return False
            except ValueError: return False
        return hmac.compare_digest(request.headers.get("Service-Key",""),configured or "xiaozhi-reminders")

    async def handle_announce(self,request):
        if not self.authorized(request):
            return web.json_response({"code":401,"msg":"提醒服务认证失败"},status=401)
        try:
            body=await request.json()
            if not isinstance(body,dict): raise ValueError()
            attempt,device=(body.get(k) for k in ('attemptId','deviceId'))
            if not all(isinstance(v,str) and v.strip() for v in (attempt,device)) or len(device)>64: raise ValueError()
            uuid.UUID(attempt)
            batch='items' in body
            if batch:
                if not isinstance(body['items'],list) or not 1<=len(body['items'])<=4: raise ValueError()
                items=[self.validate_item(item) for item in body['items']]
                if any(not item['reminderId'] or item['attemptId']==attempt for item in items): raise ValueError()
                if len({item['attemptId'] for item in items})!=len(items) or len({item['reminderId'] for item in items})!=len(items): raise ValueError()
            else:
                items=[self.validate_item(body)]
        except (ValueError,TypeError):
            return web.json_response({"code":400,"msg":"提醒参数无效"},status=400)
        now=time.monotonic()
        self.attempts={k:v for k,v in self.attempts.items() if not v[2].done() or now-v[0]<86400}
        self.batch_attempts={k:v for k,v in self.batch_attempts.items() if v in self.attempts}
        previous=self.attempts.get(attempt)
        payload=(device,batch,tuple((i['attemptId'],i['text'],i['reminderId'],i['deliveryNumber']) for i in items))
        if previous:
            if previous[1]!=payload:
                return web.json_response({"code":409,"msg":"重复发送标识内容不一致"},status=409)
            task=previous[2]
        else:
            # A member attempt cannot be replayed in a different batch or as a single request.
            if attempt in self.batch_attempts or batch and any(i['attemptId'] in self.attempts or i['attemptId'] in self.batch_attempts for i in items):
                return web.json_response({'code':409,'msg':'提醒发送标识已使用'},status=409)
            if len(self.attempts)>=512:
                return web.json_response({'code':0,**({'outcomes':{i['attemptId']:'retry' for i in items}} if batch else {'outcome':'retry'})})
            if batch:
                task=asyncio.create_task(self.deliver_batch(device,items,attempt))
                for item in items: self.batch_attempts[item['attemptId']]=attempt
            else:
                item=items[0]
                task=asyncio.create_task(self.deliver(device,item['text'],attempt,item['reminderId'],item['deliveryNumber']))
            self.attempts[attempt]=(now,payload,task)
        # 请求端超时断开不取消正在发送的音频；同attempt在本进程内共享最终回执。
        outcome=await asyncio.shield(task)
        return web.json_response({'code':0,**({'outcomes':outcome} if batch else {'outcome':outcome})})

    async def deliver(self,device,text,attempt,reminder_id=None,delivery_number=1):
        items=[dict(attemptId=attempt,text=text,reminderId=reminder_id,deliveryNumber=delivery_number)]
        return (await self.deliver_batch(device,items,attempt))[attempt]

    async def deliver_batch(self,device,items,stream):
        outcomes={item['attemptId']:'retry' for item in items}
        conn=self.ws_server.device_handlers.get(device) if self.ws_server else None
        if conn is None or conn.stop_event.is_set() or conn.tts is None: return outcomes
        generation=getattr(conn,'input_generation',0)
        session=conn.session_id
        retained=[]
        wait=getattr(conn,'reminder_music',None)
        if wait:
            from core.reminders.music import handoff_music
            retained=await handoff_music(conn)
            if retained is None: return outcomes
        def unchanged():
            return (getattr(conn,'input_generation',0)==generation and conn.session_id==session
                    and self.ws_server.device_handlers.get(device) is conn and not conn.stop_event.is_set())
        # 不抢占用户正在说话/对话，也不默认选择另一台在线设备。
        music=getattr(conn,'local_music',None)
        def music_owns_audio():
            return bool(music and music.state=='playing' and music.task and not music.task.done()
                and music.stream_id and music.stream_id==conn.sentence_id
                and not music.interrupted_for_chat and not conn.client_abort)
        # Decide eligibility before touching music. A retry must not cancel a
        # song being prepared or silence playback during a user's conversation.
        if not unchanged() or getattr(conn,'tts_finishing',False) or getattr(conn,'client_have_voice',False): return outcomes
        if music and (music.state=='loading' or music.preparing): return outcomes
        if (not getattr(conn,'standby',False) or conn.client_is_speaking or conn.chat_lock.locked()) and not music_owns_audio(): return outcomes
        if music_owns_audio():
            try:
                async with music.lock:
                    if (not music_owns_audio() or music.preparing or getattr(conn,'client_have_voice',False)
                            or getattr(conn,'tts_finishing',False)): return outcomes
                    await music.halt(for_chat=False)
            except Exception as exc:
                conn.logger.bind(tag=__name__).warning('提醒等待音乐暂停失败: {}',type(exc).__name__)
                return outcomes
        if not unchanged() or not getattr(conn,"standby",False) or conn.client_is_speaking or getattr(conn,'tts_finishing',False) or not conn.chat_lock.acquire(blocking=False): return outcomes
        music_started=False
        delivery_task=asyncio.current_task()
        conn.reminder_delivery_task=delivery_task
        current_item=None
        audio_started=False
        prepared={}
        async def active(item):
            if not item['reminderId']: return True
            from core.reminders import client as reminder_client
            snapshot=await asyncio.wait_for(reminder_client.state(device),3)
            return any(r['id']==item['reminderId'] and r.get('attemptId')==item['attemptId'] and r['status']=='dispatching' for r in snapshot['items'])
        def current():
            return unchanged() and not conn.client_abort and conn.sentence_id==stream
        async def prepare_item(item,index):
            from core.conversation.feedback import reminder_announcement
            announcement=reminder_announcement(item,index,len(items))
            audio=await asyncio.to_thread(conn.tts.to_tts,announcement)
            if isinstance(audio,str):
                from core.utils.util import audio_to_data
                audio=await audio_to_data(audio,is_opus=True)
            return announcement,audio
        async def play_item(item,index):
            nonlocal audio_started
            if not await active(item) or not current(): return 'retry'
            if index not in prepared: prepared[index]=asyncio.create_task(prepare_item(item,index))
            announcement,audio=await prepared[index]
            if not audio: return 'retry'
            if not current() or not await active(item): return 'retry'
            # Synthesize just one next item while the current audio is playing.
            # Its state is checked again before transmission; prefetch never sends audio.
            if index+1<len(items) and index+1 not in prepared:
                prepared[index+1]=asyncio.create_task(prepare_item(items[index+1],index+1))
            audio_started=True
            before=getattr(conn,'audio_flow_control',None)
            before_count=(before or {}).get('packet_count',0)
            await sendAudioMessage(conn,SentenceType.FIRST,audio,announcement,stream)
            await _wait_for_audio_completion(conn)
            after=getattr(conn,'audio_flow_control',{})
            complete=after.get('packet_count',0)==len(audio)+(before_count if after is before else 0)
            if not current() or not complete: return 'unknown'
            return 'sent'
        async def watched_item(item,index):
            playing=asyncio.create_task(play_item(item,index))
            try:
                while not playing.done():
                    done,_=await asyncio.wait({playing},timeout=0.5)
                    if done: break
                    if not current() or not await active(item):
                        playing.cancel(); await asyncio.gather(playing,return_exceptions=True)
                        return 'unknown' if audio_started else 'retry'
                return await playing
            finally:
                if not playing.done(): playing.cancel()
                await asyncio.gather(playing,return_exceptions=True)
        async def play():
            nonlocal audio_started,music_started,current_item
            conn.sentence_id=stream
            conn.client_abort=False
            conn.client_is_speaking=True
            conn.return_to_standby=True
            begin_interaction(conn)
            await send_tts_message(conn,"start",media='reminder')
            conn.tts.tts_audio_first_sentence=False  # The explicit start already binds this reminder stream.
            from core.device.face import send_face
            await send_face(conn,"reminder_call")
            for index,item in enumerate(items):
                if not current(): break
                current_item=item; audio_started=False
                outcomes[item['attemptId']]=await watched_item(item,index)
                if outcomes[item['attemptId']]=='unknown': break
            # Each member has its own delivery receipt and confirmation deadline.
            members=retained+[(i['reminderId'],i['attemptId']) for i in items if i['reminderId'] and outcomes[i['attemptId']]=='sent']
            spoken=[dict(id=i['reminderId'],title=i['text']) for i in items if i['reminderId'] and outcomes[i['attemptId']]=='sent']
            if spoken and current():
                from core.reminders.reply import remember
                remember(conn,spoken)
            if members and current():
                from core.reminders.music import MusicWait
                music=MusicWait(conn,device,members[0][0],stream,members=members)
                conn.reminder_music=music
                music.task=asyncio.create_task(music.run())
                music_started=True
            else:
                await sendAudioMessage(conn,SentenceType.LAST,[],None,stream)
        try:
            await asyncio.wait_for(play(),110)
        except (Exception,asyncio.CancelledError) as exc:
            conn.logger.bind(tag=__name__).warning("提醒发送中断: {}",type(exc).__name__)
            if current_item and outcomes[current_item['attemptId']]!='sent':
                outcomes[current_item['attemptId']]="unknown" if audio_started else "retry"
        finally:
            for task in prepared.values():
                if not task.done(): task.cancel()
            await asyncio.gather(*prepared.values(),return_exceptions=True)
            if not music_started and conn.sentence_id==stream and unchanged():
                conn.client_is_speaking=False
                try:
                    if not getattr(conn,"standby",False):
                        # 失败时停止排队音频，恢复待命，避免忙碌状态永久卡住。
                        controller=getattr(conn,"audio_rate_controller",None)
                        if controller: controller.reset()
                        await asyncio.wait_for(conn.websocket.send('{"type":"tts","state":"stop"}'),2)
                        await asyncio.wait_for(enter_standby(conn),2)
                except Exception: pass
            if not music_started: conn.chat_lock.release()
            if getattr(conn,'reminder_delivery_task',None) is delivery_task:
                conn.reminder_delivery_task=None
        return outcomes
