"""Opt-in standby: keep control transport alive without accepting microphone audio."""
import json
import time
import re
import uuid
import asyncio


def supports_standby(conn):
    return bool((getattr(conn, "features", None) or {}).get("standby_connection"))


def conversation_awake(conn):
    """A device wake-word event opens a continuing voice conversation."""
    return getattr(conn, 'conversation_awake', False) is True


def listening_allowed(conn):
    idle_reply = getattr(conn, '_idle_standby_reply', False)
    return ((conversation_awake(conn) if supports_standby(conn) else True)
            and not getattr(conn, 'explicit_standby', False)
            and (idle_reply or not getattr(conn, 'standby_after_sentence', None))
            and (idle_reply or not getattr(conn, 'return_to_standby', False))
            and not getattr(conn, 'close_after_chat', False))


def cancel_standby_reply(conn):
    if getattr(conn, '_idle_standby_reply', False):
        conn.return_to_standby = False
    conn._idle_standby_reply = False
    conn.standby_after_sentence = None
    task = getattr(conn, '_standby_reply_task', None)
    if task and task is not asyncio.current_task() and not task.done():
        task.cancel()


async def enter_standby(conn, force=False):
    cancel_standby_reply(conn)
    conn.conversation_awake = False
    if not supports_standby(conn):
        await conn.close()
        return
    conn.standby = True
    conn.input_generation = getattr(conn, 'input_generation', 0) + 1
    conn.return_to_standby = False
    conn.close_after_chat = False
    conn.client_is_speaking = False
    conn.reset_audio_states()
    message = {"type": "standby", "stream_id": getattr(conn, 'sentence_id', None)}
    if force:
        message.update(force=True, requestId=uuid.uuid4().hex)
        conn.standby_request_id = message['requestId']
        conn.explicit_standby = True
    await conn.websocket.send(json.dumps(message))
    if force and (conn.features or {}).get('standby_ack'):
        previous = getattr(conn, '_standby_retry_task', None)
        if previous and not previous.done(): previous.cancel()
        async def retry():
            try:
                for _ in range(3):
                    await asyncio.sleep(1)
                    if (getattr(conn, 'standby_request_id', None) != message['requestId']
                            or not getattr(conn, 'explicit_standby', False) or conn.stop_event.is_set()):
                        return
                    await conn.websocket.send(json.dumps(message))
                await asyncio.sleep(1)
                if getattr(conn, 'standby_request_id', None) == message['requestId'] and getattr(conn, 'explicit_standby', False):
                    conn.logger.bind(tag=__name__).warning('待命已发送，尚未取得设备状态确认')
            except Exception as error:
                conn.logger.bind(tag=__name__).warning('待命确认中断: {}', type(error).__name__)
        conn._standby_retry_task = asyncio.create_task(retry())


def begin_interaction(conn, *, awake=False):
    if awake:
        cancel_standby_reply(conn)
        conn.conversation_awake = True
        conn.return_to_standby = False
        conn.explicit_standby = False
        conn.standby_request_id = None
    conn.standby = False
    conn.close_after_chat = False
    conn.last_activity_time = time.time() * 1000
    conn.last_conversation_activity = time.monotonic()


def exit_requested(text, wake_word=''):
    """Explicit listening control; bare '休息' remains a body command."""
    if isinstance(text, str):
        text = text.strip()
        if re.search(r'(?:好吗|好不好)[？?]$', text):
            text = text[:-1]
    if not isinstance(text,str) or re.search(r'[“”"\'？?]|如果|假如|不要|不用|是不是|是否',text):
        return False
    phrase=re.sub(r'[\s，,。！!、~～]','',text)
    if wake_word and phrase.startswith(wake_word):
        phrase = phrase[len(wake_word):]
    return bool(re.fullmatch(
        r'(?:(?:小智|小兰|你|请|麻烦你|现在|先|好了|好|我们|咱们|可以|暂时))*'
        r'(?:再见|拜拜|不聊了|结束对话|结束聊天|停止聆听|停止监听|别听了|'
        r'(?:(?:去|进入|回到|回|切换到))?(?:待命|待机)(?:状态)?(?:休息|休息一下|休息一会儿)?)'
        r'(?:一下|一会儿|一会)?(?:吧|啦|了|好吗|好不好)?',phrase))


def stop_speaking_requested(text):
    return isinstance(text, str) and bool(re.fullmatch(
        r'(?:请|你|先)*(?:停|停一下|别说了|停止说话|停止播报|暂停说话|不用说了)(?:吧|了)?[。！!\s]*', text))


async def request_standby(conn, *, idle=False):
    """Speak before standby; an idle farewell stays interruptible until it ends."""
    from core.handle.abortHandle import handleAbortMessage
    if idle and (not conversation_awake(conn) or getattr(conn, 'explicit_standby', False)):
        return False
    cancel_standby_reply(conn)
    if not idle:
        # Explicit exit rejects automatic listen receipts until a real wake.
        conn.explicit_standby = True
        conn.conversation_awake = False
        conn.sentence_id = uuid.uuid4().hex
        generation = getattr(conn, 'input_generation', 0) + 1
        conn.music_chat_pending = None
        try:
            await asyncio.wait_for(handleAbortMessage(conn), 5)
        except asyncio.TimeoutError:
            conn.logger.bind(tag=__name__).warning('待命前清理超时，继续发送确认')
        if (getattr(conn, 'input_generation', 0) != generation
                or not getattr(conn, 'explicit_standby', False)):
            return False  # A real wake has superseded this exit.
    conn._idle_standby_reply = idle
    conn.sentence_id = uuid.uuid4().hex
    sentence = conn.sentence_id
    conn.standby_after_sentence = sentence
    conn.task_followup_sentence = None
    conn.return_to_standby = True
    conn.close_after_chat = False
    conn.client_abort = False
    conn.standby = False
    from core.conversation.requests import bind_sentence
    if not idle:
        bind_sentence(conn)
    if getattr(conn, 'tts', None) is None:
        await enter_standby(conn, force=True)
        return True
    from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
    conn.client_is_speaking = True
    text = '我先待命啦，需要我再叫我。' if idle else '好，我先歇会儿，需要我再叫我。'
    from core.utils.dialogue import Message
    if getattr(conn, 'dialogue', None) is not None:
        conn.dialogue.put(Message(role='assistant', content=text))
    if callable(getattr(conn.tts, 'store_tts_text', None)):
        conn.tts.store_tts_text(sentence, text)
    for kind, content in ((SentenceType.FIRST, None), (SentenceType.MIDDLE, text),
                          (SentenceType.LAST, None)):
        conn.tts.tts_text_queue.put(TTSMessageDTO(
            sentence_id=sentence, sentence_type=kind,
            content_type=ContentType.TEXT if content else ContentType.ACTION,
            content_detail=content))

    async def fallback():
        try:
            await asyncio.sleep(15)
            if (getattr(conn, 'standby_after_sentence', None) != sentence
                    or conn.sentence_id != sentence or conn.stop_event.is_set()):
                return
            conn.logger.bind(tag=__name__).warning('待命确认语音未完成，执行待命兜底')
            from core.conversation.requests import response_error
            await handleAbortMessage(conn, standby=True)
            if conn.standby:
                response_error(conn, sentence, '待命确认语音未完成，已转入待命')
        except Exception as error:
            conn.logger.bind(tag=__name__).warning('待命兜底中断: {}', type(error).__name__)
    conn._standby_reply_task = asyncio.create_task(fallback())
    return True


def queue_exit_reply(conn,text):
    if not exit_requested(text, conn.config.get('customWakeWord', '')): return False
    future = asyncio.run_coroutine_threadsafe(request_standby(conn), conn.loop)
    try:
        future.result(timeout=8)
    except TimeoutError:
        future.cancel()
        raise
    return True


async def maybe_idle_standby(conn, now=None):
    if (not supports_standby(conn) or not conversation_awake(conn) or getattr(conn,'standby',False)
            or getattr(conn, 'standby_after_sentence', None)
            or getattr(conn,'client_is_speaking',False) or getattr(conn,'tts_finishing',False)
            or getattr(conn,'client_have_voice',False)):
        return False
    lock=getattr(conn,'chat_lock',None)
    if lock and lock.locked(): return False
    worker = getattr(conn, '_asr_worker', None)
    if worker and not worker.done(): return False
    music = getattr(conn, 'local_music', None)
    if music and music.state in ('loading', 'playing'): return False
    try: seconds=float(conn.config.get('standby_idle_seconds',30))
    except (ValueError,TypeError): seconds=30
    if seconds<=0: return False
    seconds=max(10,min(seconds,600))
    last=getattr(conn,'last_conversation_activity',None)
    if last is None: return False
    if (time.monotonic() if now is None else now)-last<seconds: return False
    conn.logger.bind(tag=__name__).info('对话空闲达到 {} 秒，进入待命',seconds)
    return await request_standby(conn, idle=True)
