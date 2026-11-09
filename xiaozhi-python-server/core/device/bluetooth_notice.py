"""Speak confirmed App control events through the configured TTS provider."""
import asyncio
import json
import uuid
from collections import OrderedDict

SPEECH_READY_TIMEOUT = 4.0


async def _ack(conn, identifier, played):
    if conn.stop_event.is_set():
        return
    try:
        await conn.websocket.send(json.dumps({
            'type': 'bluetooth_control_result', 'id': identifier, 'played': played,
        }))
    except Exception:
        conn.logger.bind(tag=__name__).warning('蓝牙提示播报确认未送达：id={}', identifier)


def notice_text(enabled, success):
    if not success:
        return '这次蓝牙控制没有完成确认。'
    return 'App 已连接，进入蓝牙控制模式。' if enabled else '已退出蓝牙控制模式。'


async def handle_event(conn, message):
    identifier = message.get('id')
    if type(identifier) is not int or not 1 <= identifier <= 65535:
        return
    previous = getattr(conn, '_bluetooth_notice', None)
    if message.get('type') == 'bluetooth_control_cancel':
        if previous and previous['id'] == identifier:
            previous['cancelled'] = True
            previous['task'].cancel()
        return
    enabled, success = message.get('enabled'), message.get('success')
    if type(enabled) is not bool or type(success) is not bool:
        return
    key = (identifier, enabled, success)
    results = getattr(conn, '_bluetooth_notice_results', None)
    if results is None:
        results = conn._bluetooth_notice_results = OrderedDict()
    if key in results:
        await _ack(conn, identifier, results[key])
        return
    if previous and previous['key'] == key:
        return
    if previous:
        previous['cancelled'] = True
        previous['task'].cancel()
    pending = {'id': identifier, 'key': key, 'previous': previous['task'] if previous else None}
    conn._bluetooth_notice = pending
    task = asyncio.create_task(_run(conn, pending, notice_text(enabled, success)))
    pending['task'] = task
    conn._bluetooth_notice_task = task


async def _run(conn, pending, text):
    played = False
    began = asyncio.get_running_loop().time()
    try:
        if pending['previous']:
            # A canceled notice may need to drain its own stop message. Wait
            # only for that notice, never interrupt or queue behind user chat.
            await asyncio.wait_for(asyncio.gather(pending['previous'], return_exceptions=True), 1.5)
        else:
            await asyncio.sleep(0)
        played = await asyncio.wait_for(_speak(conn, text, pending), timeout=13)
    except asyncio.CancelledError:
        pending['reason'] = 'cancelled'
    except Exception as error:
        pending['reason'] = type(error).__name__
        conn.logger.bind(tag=__name__).warning('蓝牙提示播报未完成：id={}，{}', pending['id'], type(error).__name__)
    finally:
        log = conn.logger.bind(tag=__name__).info if played else conn.logger.bind(tag=__name__).warning
        log('蓝牙提示结果：id={} enabled={} confirmed={} played={} reason={} elapsed_ms={}',
            pending['id'], pending['key'][1], pending['key'][2], played,
            pending.get('reason', 'completed' if played else 'interrupted_or_empty_audio'),
            round((asyncio.get_running_loop().time() - began) * 1000))
        results = conn._bluetooth_notice_results
        results[pending['key']] = played
        while len(results) > 32:
            results.popitem(last=False)
        if getattr(conn, '_bluetooth_notice', None) is pending:
            conn._bluetooth_notice = None
        if getattr(conn, '_bluetooth_notice_task', None) is asyncio.current_task():
            conn._bluetooth_notice_task = None
        await _ack(conn, pending['id'], played)


def _busy_reason(conn):
    if conn.stop_event.is_set():
        return 'connection_closed'
    if not conn.tts:
        return 'tts_initializing'
    if conn.client_is_speaking or getattr(conn, 'tts_finishing', False):
        return 'speech_finishing'
    if getattr(conn, 'pending_chassis_action', None):
        return 'action_pending'
    if getattr(conn, 'reminder_delivery_task', None):
        return 'reminder_busy'
    music = getattr(conn, 'local_music', None)
    if music and music.state in ('playing', 'loading'):
        return 'music_busy'
    return None


async def _speak(conn, text, pending):
    lock = getattr(conn, 'chat_lock', None)
    if lock is None:
        pending['reason'] = 'missing_chat_lock'
        return False
    deadline = asyncio.get_running_loop().time() + SPEECH_READY_TIMEOUT
    waiting = False
    while True:
        # Task.cancel() is delivered on a later event-loop turn. Fence the
        # reservation synchronously so a released chat lock cannot revive ON
        # after OFF has already replaced it.
        if pending.get('cancelled') or getattr(conn, '_bluetooth_notice', None) is not pending:
            pending['reason'] = 'superseded'
            return False
        reason = _busy_reason(conn)
        if reason == 'connection_closed':
            pending['reason'] = reason
            return False
        if not reason and lock.acquire(blocking=False):
            break
        reason = reason or 'chat_busy'
        if not waiting:
            conn.logger.bind(tag=__name__).info('蓝牙提示等待空闲：id={} reason={}', pending['id'], reason)
            waiting = True
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            pending['reason'] = reason
            return False
        # Never hold the chat lock or block the WebSocket receive loop while
        # waiting for the previous voice-entry reply to finish.
        await asyncio.sleep(min(.05, remaining))
    sentence = str(uuid.uuid4())
    generation = getattr(conn, 'input_generation', 0)
    session = conn.session_id
    provider = conn.tts
    conn.sentence_id = sentence
    conn.client_abort = False
    started = False
    current = lambda: (not conn.stop_event.is_set() and not conn.client_abort
                       and conn.sentence_id == sentence and conn.session_id == session
                       and conn.tts is provider
                       and getattr(conn, 'input_generation', 0) == generation)
    from core.handle.sendAudioHandle import send_tts_message, sendAudio
    try:
        # This is the connection's current provider/voice; no recordings or LLM.
        audio = await asyncio.wait_for(asyncio.to_thread(provider.to_tts, text), timeout=8)
        if isinstance(audio, str):
            from core.utils.util import audio_to_data
            audio = await audio_to_data(audio, is_opus=True)
        if not audio or not current():
            return False
        await send_tts_message(conn, 'start', stream_id=sentence)
        started = True
        if not current():
            return False
        await send_tts_message(conn, 'sentence_start', text, stream_id=sentence)
        from core.reporting.chat_reports import enqueue_tts_report
        from core.utils.dialogue import Message
        enqueue_tts_report(conn, text, [audio] if isinstance(audio, bytes) else list(audio))
        conn.dialogue.put(Message(role='assistant', content=text))
        await sendAudio(conn, audio)
        if not current():
            return False
        await send_tts_message(conn, 'stop', stream_id=sentence)
        started = False
        return current()
    finally:
        try:
            if started and current():
                await asyncio.wait_for(send_tts_message(conn, 'stop', stream_id=sentence), timeout=1)
        finally:
            lock.release()
