"""Bounded, connection-bound tracking for explicit web submissions only."""
import asyncio
import time
from core.conversation.cancellation import ChatCancellation, CHAT_TIMEOUT_SECONDS

TERMINAL={'server_done','failed','unknown'}


class WebChatRequest:
    def __init__(self, request_id, fingerprint, conn, device):
        self.id=request_id; self.fingerprint=fingerprint
        self.conn=conn; self.device=device; self.session=None; self.sentence=None
        self.status='accepted'; self.message='消息已提交'
        self.created=time.monotonic(); self.updated=int(time.time()*1000)
        self.cancellation = ChatCancellation(self.created + CHAT_TIMEOUT_SECONDS)
        self.worker_future = None
        self.reply_finished=asyncio.Event()
        self.action_unconfirmed=False
        self.optional_followup=False

    def update(self, status, message):
        if self.status in TERMINAL: return
        self.status=status; self.message=message; self.updated=int(time.time()*1000)
        if status in ('failed', 'unknown'):
            self.cancellation.cancel()
            if self.worker_future is not None:
                self.worker_future.cancel()  # Only succeeds before the worker starts.

    def public(self):
        return {'requestId':self.id,'status':self.status,'message':self.message,'updatedAt':self.updated}


def bind_sentence(conn):
    record=getattr(conn,'web_chat_tracking',None)
    if record and getattr(conn,'chat_input_source',None)=='web' and record.status not in TERMINAL:
        record.sentence=conn.sentence_id


def finish_text_reply(conn):
    """A transcript-only web reply has no TTS LAST to finish its request."""
    record=getattr(conn,'web_chat_tracking',None)
    if record and record.session==conn.session_id:
        record.reply_finished.set()
        _finish_reply(record)


def _finish_reply(record):
    record.update('unknown' if record.action_unconfirmed else 'server_done',
                  '未确认底盘动作结果，请核实设备状态；不会自动重做'
                  if record.action_unconfirmed else '回复处理已结束')


def action_progress(conn, sentence, *, waiting, unconfirmed=False):
    """Track physical confirmation separately from speech, bound to this turn."""
    record=getattr(conn,'web_chat_tracking',None)
    if record and record.status not in TERMINAL and record.sentence==sentence and record.session==conn.session_id:
        record.action_unconfirmed = record.action_unconfirmed or unconfirmed
        record.update('waiting_action' if waiting else 'responding',
                      '等待底盘动作确认，请勿重复发送动作' if waiting else '正在播报动作结果')


def unconfirmed_action_result(text):
    return any(word in str(text) for word in ('超时','没收到','没有收到','没有确认','未确认','不确定'))


def response_progress(conn, sentence, finished=False):
    record=getattr(conn,'web_chat_tracking',None)
    if record and record.status not in TERMINAL and record.sentence==sentence and record.session==conn.session_id:
        if record.status != 'waiting_action':
            record.update('responding','正在处理回复')
        if finished:
            record.reply_finished.set()
            _finish_reply(record)


def response_error(conn, sentence, message='语音回复未完成，实际操作结果请核实；不会自动重做'):
    record=getattr(conn,'web_chat_tracking',None)
    if sentence is not None and record and record.status not in TERMINAL and record.sentence==sentence and record.session==conn.session_id:
        # Invoked from TTS workers too; update only this bound request.
        if not finish_optional_followup(record):
            record.update('unknown',message)
        pending=getattr(conn,'pending_chassis_action',None)
        if pending and pending.get('sentence_id')==sentence:
            conn.pending_chassis_action=None


def followup_progress(conn, sentence):
    """Main audio is complete; failure of an optional notice cannot undo that."""
    record=getattr(conn,'web_chat_tracking',None)
    if record and record.status not in TERMINAL and record.sentence==sentence and record.session==conn.session_id:
        record.optional_followup=True
        record.update('responding','正在补充未确认提醒')


def finish_optional_followup(record):
    if not record.optional_followup or record.action_unconfirmed:
        return False
    record.reply_finished.set()
    record.update('server_done','回复已结束，补充提醒未完成，原提醒仍未确认')
    return True
