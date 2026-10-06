"""Durable FIFO outbox. One worker per actual API owner/role, shared by connections."""
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from weakref import WeakKeyDictionary

from .models import SaveResult


def scope_key(role):
    from config.manage_api_client import memory_headers, _java_base_url
    identity = json.dumps([_java_base_url(), memory_headers(), role], sort_keys=True)
    return hashlib.sha256(identity.encode()).hexdigest()


def trivial(text):
    # Whole-message matches only. No topic/number/negation heuristics.
    return re.sub(r'[\s，。！？!?,.]', '', text) in {'你好', '您好', '谢谢', '谢谢你', '哈哈', '早上好', '晚安'}


def needs_confirmation(text):
    # Routing only; V2 still decides meaning and validates every operation.
    return bool(re.search(r'记住|记一下|记下来|记着|记到|长期记忆|忘掉|忘记|别记|不要记|删除.*记忆|remember|forget|save.{0,30}memory', text, re.I))


class MemoryQueue:
    def __init__(self, path, max_pending=256, retry_delays=(1, 3)):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # FIFO is owned by one server process, not one WebSocket connection.
        # Refuse a second writer process instead of silently racing the outbox.
        self.lease = open(str(path) + '.lock', 'a+b')
        if self.lease.tell() == 0:
            self.lease.write(b'0'); self.lease.flush()
        self.lease.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.lease.close()
            raise RuntimeError('memory_outbox_already_owned') from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, session TEXT NOT NULL,
            message TEXT NOT NULL, content TEXT NOT NULL, observed TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            proposal TEXT, result TEXT, UNIQUE(scope,session,message))''')
        self.db.commit()
        self.providers, self.tasks, self.waiters = {}, {}, {}
        self.max_pending, self.retry_delays = max_pending, retry_delays

    def close(self):
        self.db.close()
        self.lease.close()

    def bind(self, scope, provider):
        self.providers[scope] = provider
        task = self.tasks.get(scope)
        if task is None or task.done():
            self.tasks[scope] = asyncio.create_task(self._worker(scope), name='memory-worker-' + scope[:8])
            def completed(done):
                if not done.cancelled() and done.exception() is not None:
                    provider._log('worker_stopped', {'scope': scope, 'type': type(done.exception()).__name__})
            self.tasks[scope].add_done_callback(completed)

    def enqueue(self, scope, provider, message, session):
        row = self.db.execute('SELECT * FROM jobs WHERE scope=? AND session=? AND message=?',
                              (scope, session, str(message.uniq_id))).fetchone()
        if row is None:
            count = self.db.execute("SELECT count(*) FROM jobs WHERE scope=? AND state IN ('pending','blocked')", (scope,)).fetchone()[0]
            if count >= self.max_pending:
                provider._log('queue_full', {'scope': scope, 'messageId': message.uniq_id})
                raise RuntimeError('memory_queue_full')
            with self.db:
                cursor = self.db.execute('INSERT INTO jobs(scope,session,message,content,observed) VALUES(?,?,?,?,?)',
                    (scope, session, str(message.uniq_id), message.content, message.created_at.astimezone().isoformat()))
            row = self.db.execute('SELECT * FROM jobs WHERE id=?', (cursor.lastrowid,)).fetchone()
            provider._log('queued', {'messageId': message.uniq_id, 'jobId': row['id']})
        future = self.waiters.get(row['id'])
        if future is None:
            future = asyncio.get_running_loop().create_future()
            if row['state'] != 'pending':
                value = json.loads(row['result']) if row['result'] else {'status': 'failed'}
                future.set_result(SaveResult(**value))
            else:
                self.waiters[row['id']] = future
        self.bind(scope, provider)
        return future

    def checkpoint(self, job_id, payload):
        # Must finish durable write BEFORE the network Commit is attempted.
        with self.db:
            self.db.execute('UPDATE jobs SET proposal=? WHERE id=?', (json.dumps(payload, ensure_ascii=False), job_id))

    async def _worker(self, scope):
        while True:
            row = self.db.execute("SELECT * FROM jobs WHERE scope=? AND state IN ('pending','blocked') ORDER BY id LIMIT 1", (scope,)).fetchone()
            if row is None or row['state'] == 'blocked':
                return
            provider = self.providers[scope]
            current = SimpleNamespace(role='user', content=row['content'], uniq_id=row['message'],
                                      created_at=datetime.fromisoformat(row['observed']), is_user_input=True, is_temporary=False)
            key = (provider.role_id, row['session'], row['message'])
            if row['proposal']:
                provider._pending_commits[key] = json.loads(row['proposal'])
            started = time.monotonic()
            if row['attempts'] >= len(self.retry_delays) + 1:
                result = None
            else:
                with self.db:
                    self.db.execute('UPDATE jobs SET attempts=attempts+1 WHERE id=?', (row['id'],))
                try:
                    result = await provider._process([current], row['session'],
                        checkpoint=lambda payload: self.checkpoint(row['id'], payload))
                except Exception as error:
                    provider._log('worker_failed', {'messageId': row['message'], 'type': type(error).__name__})
                    result = None
            if result is None:
                updated = self.db.execute('SELECT attempts,proposal FROM jobs WHERE id=?', (row['id'],)).fetchone()
                if updated['attempts'] <= len(self.retry_delays):
                    provider._log('retry_scheduled', {'messageId': row['message'], 'attempt': updated['attempts']})
                    await asyncio.sleep(self.retry_delays[updated['attempts'] - 1])
                    continue
                state = 'blocked' if updated['proposal'] else 'failed'
                result = SaveResult('failed')
            else:
                state = result.status
            with self.db:
                self.db.execute('UPDATE jobs SET state=?,result=? WHERE id=?',
                    (state, json.dumps({'status': result.status, 'operations': result.operations, 'counts': result.counts}, ensure_ascii=False), row['id']))
                # Keep a bounded receipt history; unresolved proposals are never purged.
                self.db.execute("DELETE FROM jobs WHERE scope=? AND state NOT IN ('pending','blocked') AND id NOT IN (SELECT id FROM jobs WHERE scope=? ORDER BY id DESC LIMIT 1024)", (scope, scope))
            provider._log('queue_result', {'messageId': row['message'], 'status': state,
                                         'elapsedMs': int((time.monotonic() - started) * 1000)})
            waiter = self.waiters.pop(row['id'], None)
            if waiter and not waiter.done():
                waiter.set_result(result)
            if state == 'blocked':
                provider._log('queue_blocked', {'scope': scope, 'jobId': row['id'], 'reason': 'commit_unconfirmed_retry_limit'})
                return


_queues = WeakKeyDictionary()


def get_queue():
    loop = asyncio.get_running_loop()
    if loop not in _queues:
        default = Path(__file__).resolve().parents[4] / 'data' / 'memory-outbox.sqlite3'
        _queues[loop] = MemoryQueue(os.environ.get('XIAOZHI_MEMORY_OUTBOX', str(default)))
    return _queues[loop]
