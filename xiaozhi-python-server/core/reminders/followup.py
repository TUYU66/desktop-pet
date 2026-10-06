"""Catch up on unanswered/expired reminders during an explicit interaction."""
import asyncio
import time
import uuid
from core.reminders import client as reminder_client


def stage(item):
    status = item.get('status')
    if status == 'retry_pending':
        return item.get('nextAttemptAt', 0)
    code = {'expired': 2, 'missed': 3, 'delivery_unknown': 4}.get(status)
    return item.get('updatedAt', 0) * 8 + code if code else 0


def notice_text(item, remaining=0):
    title = str(item['title'])
    if len(title) > 36:
        title = title[:36] + '…'
    at = item.get('scheduledAt') or item.get('originalDueAt') or item.get('dueAt')
    if at:
        from core.conversation.feedback import spoken_date
        when = spoken_date(at)
        prefix = f'之前安排在{when}的“{title}”提醒'
    else:
        prefix = f'之前的“{title}”提醒'
    detail = {
        'retry_pending': '还没听你说收到，过会儿我再叫你。',
        'expired': '已经逾期了，我先不自动重复提醒了。',
        'missed': '我叫过你几次，还没听到回应，就先不继续重复了。',
        'delivery_unknown': '有没有播到，我还没确认，你帮我看一下有没有收到。',
    }[item['status']]
    text = '顺便提醒一下，' + prefix + '，' + detail
    if remaining > 0:
        text += f'另外还有{remaining}条还没回应，你在日程页面看一下吧。'
    return text


async def command(device, body):
    return await asyncio.wait_for(reminder_client.command(device, body), 3)


async def deliver(conn, sentence_id, *, standalone=False, text_only=False):
    if not sentence_id or getattr(conn, 'task_followup_sentence', None) != sentence_id:
        return False
    pending = getattr(conn, 'pending_chassis_action', None)
    if pending and pending.get('sentence_id') == sentence_id:
        return False  # Preserve the marker for the action's outcome, after it completes.
    conn.task_followup_sentence = None
    if any(getattr(conn, attr, None) for attr in (
            'schedule_pending', 'schedule_batch_pending', 'schedule_edit_pending', 'schedule_control_pending')):
        return False  # Never change choice versions or distract from an active question.
    device = getattr(conn, 'headers', {}).get('device-id', '')
    if not device:
        return False
    generation = getattr(conn, 'input_generation', 0)
    session = conn.session_id
    current = lambda: (not conn.client_abort and not conn.stop_event.is_set()
                       and conn.sentence_id == sentence_id and conn.session_id == session
                       and getattr(conn, 'input_generation', 0) == generation)
    if not current():
        return False
    claim = None
    finished = False
    task = asyncio.current_task()
    tracked = False
    try:
        from core.handle.sendAudioHandle import _wait_for_audio_completion, sendAudio, send_tts_message
        if not text_only:
            await _wait_for_audio_completion(conn)
        if not current():
            return False
        # Only the optional notice is interruptible, after the main reply drains.
        conn.task_followup_task = task
        tracked = True
        from core.conversation.requests import followup_progress
        followup_progress(conn, sentence_id)
        snapshot = await asyncio.wait_for(reminder_client.catchup(device), 3)
        if not current():
            return False
        for item in snapshot['items']:
            expected = stage(item)
            if not expected:
                continue
            token = str(uuid.uuid4())
            body = dict(id=item['id'], token=token, stage=expected)
            result = await command(device, dict(body, action='followup_claim'))
            if not result.get('claimed'):
                continue
            claim = body
            if not current():
                return False
            text = notice_text(item, max(0, snapshot.get('count', 1) - 1))
            audio = []
            reported = False
            def report_notice():
                from core.handle.reportHandle import enqueue_tts_report
                from core.utils.dialogue import Message
                from core.reminders.reply import remember
                enqueue_tts_report(conn, text, audio)
                conn.dialogue.put(Message(role='assistant', content=text))
                remember(conn,snapshot['items'],snapshot.get('count',len(snapshot['items'])))
                conn.reminder_response_until = time.monotonic() + 90
            if getattr(conn, 'chat_input_source', None) == 'web':
                valid = await command(device, dict(body, action='followup_valid'))
                if not valid.get('valid') or not current():
                    return False
                # Publish before synthesis, so an interrupted notice remains visible.
                report_notice()
                reported = True
            if not text_only:
                synthesis = asyncio.create_task(asyncio.to_thread(conn.tts.to_tts, text))
                conn.task_followup_audio_task = synthesis
                try:
                    audio = await asyncio.wait_for(synthesis, 10)
                finally:
                    if getattr(conn, 'task_followup_audio_task', None) is synthesis:
                        conn.task_followup_audio_task = None
                if isinstance(audio, str):
                    from core.utils.util import audio_to_data
                    audio = await audio_to_data(audio, is_opus=True)
                if not audio or not current():
                    return False
            valid = await command(device, dict(body, action='followup_valid'))
            if not valid.get('valid') or not current():
                return False
            if not text_only:
                if standalone:
                    await send_tts_message(conn, 'start', stream_id=sentence_id)
                await send_tts_message(conn, 'sentence_start', text, stream_id=sentence_id)
                await sendAudio(conn, audio)
                await asyncio.wait_for(_wait_for_audio_completion(conn), 40)
                if not current():
                    return False
            if not reported:
                report_notice()
            finished = bool((await command(device, dict(body, action='followup_finish'))).get('finished'))
            return True
        return False
    except asyncio.CancelledError:
        return False  # Barge-in takes precedence over the optional catch-up speech.
    except Exception as exc:
        conn.logger.bind(tag=__name__).warning('补充提醒未完成，原提醒时间与状态保持不变: {}', type(exc).__name__)
        return False
    finally:
        if claim and not finished:
            try:
                await command(device, dict(claim, action='followup_release'))
            except (Exception, asyncio.CancelledError):
                # A failed release expires automatically; it never consumes this notice forever.
                conn.logger.bind(tag=__name__).warning('补提醒认领释放未确认，租约到期后可重试')
        if tracked and getattr(conn, 'task_followup_task', None) is task:
            conn.task_followup_task = None


def queue_on_wake(conn):
    """Greeting-disabled wakes still announce pending notices, without blocking input."""
    previous = getattr(conn, 'task_followup_wake_task', None)
    if previous and not previous.done():
        return
    generation = getattr(conn, 'input_generation', 0)
    session = conn.session_id

    async def run():
        if (conn.stop_event.is_set() or conn.standby or conn.client_is_speaking
                or getattr(conn, 'input_generation', 0) != generation or conn.session_id != session
                or not conn.chat_lock.acquire(blocking=False)):
            return
        sentence = str(uuid.uuid4())
        conn.sentence_id = sentence
        conn.task_followup_sentence = sentence
        conn.client_abort = False
        try:
            await deliver(conn, sentence, standalone=True)
        finally:
            try:
                if (conn.sentence_id == sentence and not conn.client_abort
                        and getattr(conn, 'input_generation', 0) == generation
                        and conn.client_is_speaking):
                    from core.handle.sendAudioHandle import send_tts_message
                    await send_tts_message(conn, 'stop', stream_id=sentence)
            except Exception as exc:
                conn.logger.bind(tag=__name__).warning('唤醒补提醒收尾失败: {}', type(exc).__name__)
            finally:
                conn.chat_lock.release()
    conn.task_followup_wake_task = asyncio.create_task(run())
