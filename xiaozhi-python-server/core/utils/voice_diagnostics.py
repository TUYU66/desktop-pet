"""Low-rate, content-free counters for locating a stalled voice input path.

Call from the connection event loop; this does not change listening state.
"""
import time
from collections import Counter


def count(conn, stage):
    if not hasattr(conn, '_voice_diagnostics'):
        conn._voice_diagnostics = Counter()
    conn._voice_diagnostics[stage] += 1


def report(conn):
    now = time.monotonic()
    previous = getattr(conn, '_voice_diagnostics_at', None)
    if previous is None:
        conn._voice_diagnostics_at = now
        return
    if now - previous < 10:
        return
    conn._voice_diagnostics_at = now
    counters = getattr(conn, '_voice_diagnostics', Counter())
    conn._voice_diagnostics = Counter()
    awake = getattr(conn, 'conversation_awake', False) is True
    if not awake and not counters:
        return
    worker = getattr(conn, '_asr_worker', None)
    queue = getattr(conn, 'asr_audio_queue', None)
    utterances = getattr(conn, '_asr_utterances', None)
    conn.logger.bind(tag=__name__).info(
        'voice_input window_s={} rx={} processed={} voiced={} blocked_awake={} '
        'blocked_standby={} blocked_music={} warmup={} unready={} overflow={} '
        'queued={} asr_start={} asr_text={} asr_empty={} asr_stale={} asr_timeout={} '
        'awake={} standby={} speaking={} mode={} packet_queue={} utterance_queue={} worker={}',
        round(now - previous, 1),
        *(counters[name] for name in ('rx', 'processed', 'voiced', 'blocked_awake',
          'blocked_standby', 'blocked_music', 'warmup', 'unready', 'overflow',
          'queued', 'asr_start', 'asr_text', 'asr_empty', 'asr_stale', 'asr_timeout')),
        awake, getattr(conn, 'standby', False), getattr(conn, 'client_is_speaking', False),
        getattr(conn, 'client_listen_mode', None), queue.qsize() if queue is not None else 0,
        utterances.qsize() if utterances is not None else 0,
        bool(worker and not worker.done()))
