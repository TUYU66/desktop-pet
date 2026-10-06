"""Turn-owned cancellation; never unlock a worker or replay an external action."""
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import TimeoutError as FutureTimeout
import threading
import time

CHAT_TIMEOUT_SECONDS = 180
_current = ContextVar('chat_cancellation', default=None)


class ChatTurnCancelled(Exception):
    pass


class ChatCancellation:
    def __init__(self, deadline):
        self.deadline = deadline
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.streams = set()

    def cancelled(self):
        return self.event.is_set() or time.monotonic() >= self.deadline

    def check(self):
        if self.cancelled():
            raise ChatTurnCancelled('chat turn cancelled or timed out')

    def register(self, stream):
        with self.lock:
            cancelled = self.cancelled()
            if not cancelled:
                self.streams.add(stream)
        if cancelled:
            stream.close()
            self.check()

    def unregister(self, stream):
        with self.lock:
            self.streams.discard(stream)

    def cancel(self):
        self.event.set()
        with self.lock:
            streams = tuple(self.streams)
            self.streams.clear()
        if streams:
            # Closing a socket may block. Never do that on the asyncio event loop.
            def close_streams():
                for stream in streams:
                    try:
                        stream.close()
                    except Exception:
                        pass
            threading.Thread(target=close_streams, name='close-chat-stream', daemon=True).start()


def current_turn():
    return _current.get()


@contextmanager
def without_chat_cancellation():
    """Persistent background work must not inherit a foreground reply's lifetime."""
    token = _current.set(None)
    try:
        yield
    finally:
        _current.reset(token)


def chat_cancelled():
    turn = current_turn()
    return bool(turn and turn.cancelled())


def cancel_active_chat(conn):
    turn = getattr(conn, '_active_chat_cancellation', None)
    if isinstance(turn, ChatCancellation):
        turn.cancel()
    record = getattr(conn, 'web_chat_tracking', None)
    worker = getattr(record, 'worker_future', None)
    pending = getattr(record, 'cancellation', None)
    if isinstance(pending, ChatCancellation) and worker is not None and not worker.done():
        pending.cancel()
        worker.cancel()  # Its registered callback releases a never-started reservation.


@contextmanager
def chat_scope(conn, cancellation=None):
    turn = cancellation or ChatCancellation(time.monotonic() + CHAT_TIMEOUT_SECONDS)
    token = _current.set(turn)
    conn._active_chat_cancellation = turn
    timer = threading.Timer(max(0, turn.deadline - time.monotonic()), turn.cancel)
    timer.daemon = True
    timer.start()
    try:
        turn.check()
        yield turn
    finally:
        timer.cancel()
        turn.cancel()
        _current.reset(token)
        if getattr(conn, '_active_chat_cancellation', None) is turn:
            conn._active_chat_cancellation = None


def wait_chat_future(future, timeout=None):
    """Stop waiting promptly; an already dispatched tool is not cancelled/retried."""
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        turn = current_turn()
        if turn:
            turn.check()
        remaining = deadline - time.monotonic() if deadline is not None else .2
        if remaining <= 0:
            raise FutureTimeout()
        try:
            result = future.result(timeout=min(.2, remaining))
        except FutureTimeout:
            # A completed future may itself have raised TimeoutError.
            if future.done():
                raise
            continue
        if turn:
            turn.check()
        return result


async def cleanup_cancelled_voice(conn, turn):
    """Finish timeout cleanup while the worker still owns the conversation lock."""
    import asyncio
    import json
    if getattr(conn, '_active_chat_cancellation', None) is not turn or not turn.cancelled():
        return
    if getattr(conn, 'client_abort', False):
        return  # An explicit device/web abort already owns cleanup.
    conn.client_abort = True
    conn.input_generation = getattr(conn, 'input_generation', 0) + 1
    conn.pending_chassis_action = None
    conn.clear_queues()
    conn.clearSpeakStatus()
    controller = getattr(conn, 'audio_rate_controller', None)
    if controller:
        controller.reset()
    from core.conversation.standby import listening_allowed
    await asyncio.wait_for(conn.websocket.send(json.dumps({
        'type':'tts', 'state':'stop', 'session_id':conn.session_id,
        'aborted':True, 'listen_after':listening_allowed(conn),
    })), 2)
