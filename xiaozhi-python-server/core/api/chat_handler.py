"""聊天文字输入 + 记忆管理 HTTP API"""

import os
import yaml
import json
import asyncio
import time
import uuid
import re
from core.conversation.requests import WebChatRequest, TERMINAL, finish_optional_followup
from core.conversation.cancellation import CHAT_TIMEOUT_SECONDS
from aiohttp import web
from config.logger import setup_logging
from config.config_loader import get_project_dir

TAG = __name__


class ChatHandler:
    """处理前端文字发送、记忆清理等操作"""

    def __init__(self, config: dict, ws_server=None):
        self.config = config
        self.ws_server = ws_server
        self.logger = setup_logging()
        self.requests = {}
        self.expired = {}

    def prune_requests(self):
        now=time.monotonic()
        for key,record in list(self.requests.items()):
            if record.task.done() and now-record.created>3600:
                self.expired[key]=now
                del self.requests[key]
        self.expired={k:v for k,v in self.expired.items() if now-v<86400}
        while len(self.expired)>4096: self.expired.pop(next(iter(self.expired)))

    def current(self, record):
        return bool(self.ws_server and self.ws_server.device_handlers.get(record.device) is record.conn
                    and not record.conn.stop_event.is_set())

    async def handle_request_status(self, request):
        self.prune_requests()
        key=request.match_info['requestId']
        record=self.requests.get(key)
        if not record:
            expired=key in self.expired
            return web.json_response({'code':410 if expired else 404,
                'msg':'请求记录已过期，结果待核实' if expired else '未找到请求记录，结果待核实'},status=410 if expired else 404)
        if record.status not in TERMINAL and (not self.current(record) or time.monotonic()-record.created>CHAT_TIMEOUT_SECONDS):
            if not finish_optional_followup(record):
                record.update('unknown','连接已变化或处理超时，结果待核实，请勿自动重发')
        return web.json_response({'code':0,'data':record.public()})

    async def handle_send(self, request):
        try:
            body=await request.json()
            if not isinstance(body,dict) or any(not isinstance(body.get(k,''),str) for k in ('text','deviceId','sessionId','requestId')):
                raise ValueError('聊天参数必须为字符串')
            text=body.get('text','').strip()
            if not text or len(text)>20000: raise ValueError('消息需为1至20000字')
            key=body.get('requestId') or str(uuid.uuid4())
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',key): raise ValueError('请求编号无效')
            device,session=body.get('deviceId',''),body.get('sessionId','')
            fingerprint=(text,device,session)
            self.prune_requests()
            old=self.requests.get(key)
            if old:
                if old.fingerprint!=fingerprint:
                    return web.json_response({'code':409,'msg':'同一请求编号不能用于不同消息'},status=409)
                if old.status not in TERMINAL and not self.current(old): old.update('unknown','设备连接已变化，结果待核实')
                return web.json_response({'code':0,'data':old.public()})
            if key in self.expired:
                return web.json_response({'code':410,'msg':'请求记录已过期，请先核实实际结果'},status=410)
            devices=self.ws_server.device_handlers if self.ws_server else {}
            if not device: device=next((d for d,c in devices.items() if not c.stop_event.is_set()),'')
            conn=devices.get(device)
            if not conn: return web.json_response({'code':404,'msg':'没有在线设备'},status=404)
            if conn.stop_event.is_set() or conn.tts is None:
                return web.json_response({'code':503,'msg':'设备正在连接，请稍后重试'},status=503)
            if len(self.requests)>=512:
                return web.json_response({'code':503,'msg':'请求记录容量已满，请稍后再试'},status=503)
            record=WebChatRequest(key,fingerprint,conn,device)
            self.requests[key]=record
            record.task=asyncio.create_task(self.execute(record,text,session),name='web-chat-request')
            return web.json_response({'code':0,'msg':'消息已提交','data':record.public()})
        except (ValueError,TypeError):
            return web.json_response({'code':400,'msg':'聊天请求格式或参数无效'},status=400)

    async def await_reply(self, record):
        conn=record.conn
        while record.status not in TERMINAL:
            if record.reply_finished.is_set():
                record.update('server_done','回复处理已结束'); return
            if not self.current(record) or conn.session_id!=record.session or conn.client_abort:
                if not finish_optional_followup(record):
                    record.update('unknown','对话已中断或连接已变化，结果待核实')
                return
            if record.sentence and conn.sentence_id!=record.sentence:
                record.update('unknown','新的对话已开始，上一条结果待核实'); return
            if time.monotonic()-record.created>CHAT_TIMEOUT_SECONDS:
                if not finish_optional_followup(record):
                    record.update('unknown','处理超时，结果待核实')
                return
            await asyncio.sleep(.1)

    async def execute(self, record, text, session):
        conn=record.conn
        owned=False
        was_standby=getattr(conn,'standby',False) is True
        try:
            if not self.current(record):
                record.update('unknown','设备连接已变化，结果待核实'); return
            previous=getattr(conn,'web_chat_tracking',None)
            from core.conversation.standby import exit_requested, stop_speaking_requested, request_standby
            if exit_requested(text, conn.config.get('customWakeWord', '')) or stop_speaking_requested(text):
                if previous and previous.status not in TERMINAL:
                    if not finish_optional_followup(previous):
                        previous.update('unknown', '用户已中断，之前的操作结果请核实')
                from core.handle.sendAudioHandle import send_stt_message
                if session and conn.session_id != session:
                    await conn.switch_session(session)
                record.session = conn.session_id
                from core.handle.reportHandle import enqueue_asr_report
                from core.utils.dialogue import Message
                enqueue_asr_report(conn, text, [])
                conn.dialogue.put(Message(role='user', content=text))
                await send_stt_message(conn, text)
                if exit_requested(text, conn.config.get('customWakeWord', '')):
                    conn.web_chat_tracking = record
                    record.session = conn.session_id
                    record.update('processing', '正在确认待命')
                    if not await request_standby(conn):
                        record.update('unknown', '新的对话已开始，待命请求已取消')
                        return
                    record.sentence = conn.sentence_id
                    if conn.standby:
                        record.update('server_done', '待命指令已发送')
                    else:
                        await self.await_reply(record)
                    return
                else:
                    from core.handle.abortHandle import handleAbortMessage
                    await handleAbortMessage(conn)
                record.update('server_done', '控制指令已发送')
                return
            if previous and not previous.task.done():
                music=getattr(conn,'local_music',None)
                # Playback starts only after its acknowledgement and audio lock
                # have been released. The request waiter may still be unwinding.
                music_handoff=(music is not None and music.state=='playing'
                    and getattr(music,'awaiting_sentence',None)==previous.sentence
                    and previous.sentence is not None and previous.session==conn.session_id)
                if previous.reply_finished.is_set() or music_handoff:
                    previous.reply_finished.set()
                    previous.update('server_done','回复处理已结束')
                else:
                    record.update('failed','正在回复上一条消息，请稍后再发送'); return
            conn.web_chat_tracking=record
            record.session=conn.session_id
            record.update('processing','正在处理消息')
            music=getattr(conn,'local_music',None)
            self.logger.bind(tag=TAG).info('网页请求开始: request_id={}, music_state={}',record.id,getattr(music,'state','stopped'))
            from core.music.conversation import handle_input
            before_sentence=conn.sentence_id
            # This route can await music cancellation before it reaches the
            # normal reply deadline. Never leave that phase unbounded.
            handled,routed_text=await asyncio.wait_for(handle_input(conn,text,web=True),45)
            if handled:
                if record.status in TERMINAL:
                    return
                record.sentence=conn.sentence_id
                if before_sentence==conn.sentence_id:
                    record.update('server_done','回复处理已结束')
                else:
                    record.update('responding','正在处理回复')
                    await self.await_reply(record)
                return
            from core.music import pause_for_chat
            await pause_for_chat(conn,for_chat=False)
            if conn.client_is_speaking or not conn.chat_lock.acquire(blocking=False):
                record.update('failed','正在回复，请稍后再发送'); return
            owned=True
            if session and conn.session_id!=session: await conn.switch_session(session)
            record.session=conn.session_id
            from core.handle.sendAudioHandle import send_stt_message
            from core.conversation.standby import begin_interaction, conversation_awake
            conn.return_to_standby = not conversation_awake(conn)
            begin_interaction(conn)
            conn.client_abort=False
            await send_stt_message(conn,routed_text)
            future=conn.executor.submit(conn.run_web_chat,routed_text,record)
            owned=False  # run_web_chat owns release from here, including exceptions.
            def cancelled(done):
                if done.cancelled(): conn.chat_lock.release()  # Never started, no wrapper finally.
            future.add_done_callback(cancelled)
            record.worker_future = future
            # Bound the HTTP tracker, without cancelling/replaying work that may
            # already have performed an external action. The worker owns its lock.
            remaining=max(.1,CHAT_TIMEOUT_SECONDS-(time.monotonic()-record.created))
            result=await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)),remaining)
            if result is not True:
                if finish_optional_followup(record):
                    return
                if not self.current(record) or conn.session_id!=record.session or conn.client_abort:
                    record.update('unknown','对话已中断或连接已变化，结果待核实')
                else:
                    record.update('unknown','回复生成未完成，实际操作结果请核实；不会自动重做')
                return
            if record.status != 'waiting_action':
                record.update('responding','正在处理回复')
            await self.await_reply(record)
        except asyncio.CancelledError:
            if not finish_optional_followup(record):
                record.update('unknown','处理已中断，结果待核实')
            raise
        except asyncio.TimeoutError:
            if not finish_optional_followup(record):
                record.update('unknown','处理超时，旧回复已取消；实际操作结果请核实，不会自动重做')
        except Exception as exc:
            self.logger.bind(tag=TAG).error('网页请求 {} 处理异常: {}',record.id,type(exc).__name__)
            if not finish_optional_followup(record):
                record.update('unknown','回复处理异常，实际操作结果请核实；不会自动重做')
        finally:
            self.logger.bind(tag=TAG).info('网页请求结束: request_id={}, status={}',record.id,record.status)
            if owned: conn.chat_lock.release()
            if getattr(conn,'web_chat_tracking',None) is record:
                conn.web_chat_tracking=None
                if (record.status in ('failed','unknown') and self.current(record)
                        and conn.session_id==record.session and conn.sentence_id==record.sentence):
                    conn.client_abort=True
                    conn.input_generation = getattr(conn, 'input_generation', 0) + 1
                    clear_queues = getattr(conn, 'clear_queues', None)
                    if callable(clear_queues):
                        clear_queues()
                    music=getattr(conn,'local_music',None)
                    if music and getattr(music,'awaiting_sentence',None)==record.sentence and music.state=='loading':
                        await music.halt(paused=True)
                    pending=getattr(conn,'pending_chassis_action',None)
                    if pending and pending.get('sentence_id')==record.sentence: conn.pending_chassis_action=None
                    conn.client_is_speaking=False
                    try:
                        controller=getattr(conn,'audio_rate_controller',None)
                        if controller: controller.reset()
                        await asyncio.wait_for(conn.websocket.send(json.dumps({'type':'tts','state':'stop','session_id':conn.session_id})),2)
                        if was_standby:
                            from core.conversation.standby import enter_standby
                            await asyncio.wait_for(enter_standby(conn),2)
                    except Exception: pass

    async def handle_clear_memory(self, request: web.Request) -> web.Response:
        """清除指定会话的记忆文件
        从 data/.memory.yaml 中删除对应 session_id 条目
        """
        try:
            session_id = request.match_info.get("sessionId", "")
            if not session_id:
                return web.json_response({"code": 400, "msg": "sessionId 不能为空"}, status=400)

            memory_path = get_project_dir() + "data/.memory.yaml"
            if os.path.exists(memory_path):
                with open(memory_path, "r", encoding="utf-8") as f:
                    all_memory = yaml.safe_load(f) or {}
                if session_id in all_memory:
                    del all_memory[session_id]
                    with open(memory_path, "w", encoding="utf-8") as f:
                        yaml.dump(all_memory, f, allow_unicode=True)
                    self.logger.bind(tag=TAG).info(f"已清除会话 {session_id} 的记忆")

            # 也要清除当前运行时 memory provider 中的记忆
            if self.ws_server:
                for handler in self.ws_server.device_handlers.values():
                    if hasattr(handler, "memory") and handler.memory:
                        try:
                            if handler.session_id == session_id:
                                handler.memory.short_memory = ""
                        except Exception:
                            pass

            return web.json_response({"code": 0, "msg": "记忆已清除"})

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"清除记忆失败: {e}")
            return web.json_response({"code": 500, "msg": str(e)}, status=500)
