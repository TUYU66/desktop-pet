import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.providers.memory.memory_v2.background import MemoryQueue, trivial, needs_confirmation
from core.providers.memory.memory_v2.cache import RecallCache
from core.providers.memory.memory_v2.models import SaveResult
from core.providers.memory.memory_v2.memory_v2 import MemoryProvider


def message(text, ident):
    return SimpleNamespace(content=text, uniq_id=ident, created_at=datetime.now().astimezone(), role='user')


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'queue.db'
        self.queue = MemoryQueue(self.path, retry_delays=(0, 0))
        self.provider = SimpleNamespace(role_id='role', _pending_commits={}, _log=Mock())

    async def asyncTearDown(self):
        for task in self.queue.tasks.values():
            task.cancel()
        await asyncio.gather(*self.queue.tasks.values(), return_exceptions=True)
        self.queue.close()
        self.temp.cleanup()

    async def test_same_scope_order_and_other_scope_parallel(self):
        gate = asyncio.Event()
        seen = []
        async def process(messages, session, checkpoint=None):
            ident = messages[0].uniq_id
            seen.append(ident)
            if ident == '1': await gate.wait()
            return SaveResult('skipped')
        self.provider._process = process
        first = self.queue.enqueue('a', self.provider, message('第一条', '1'), 's')
        second = self.queue.enqueue('a', self.provider, message('第二条', '2'), 's')
        other = self.queue.enqueue('b', self.provider, message('其他用户', '3'), 's')
        await other
        self.assertEqual(seen, ['1', '3'])
        self.assertFalse(second.done())
        gate.set()
        await asyncio.gather(first, second)
        self.assertEqual(seen, ['1', '3', '2'])

    async def test_rejection_is_not_retried_and_duplicate_is_receipt(self):
        self.provider._process = AsyncMock(return_value=SaveResult('rejected'))
        msg = message('一个请求', '1')
        result = await self.queue.enqueue('a', self.provider, msg, 's')
        again = await self.queue.enqueue('a', self.provider, msg, 's')
        self.assertEqual(result.status, again.status)
        self.assertEqual(self.provider._process.await_count, 1)

    async def test_transient_failure_retries_before_next(self):
        seen = []
        async def process(messages, session, checkpoint=None):
            ident = messages[0].uniq_id
            seen.append(ident)
            return None if len(seen) == 1 else SaveResult('skipped')
        self.provider._process = process
        first = self.queue.enqueue('a', self.provider, message('第一条', '1'), 's')
        second = self.queue.enqueue('a', self.provider, message('第二条', '2'), 's')
        await asyncio.gather(first, second)
        self.assertEqual(seen, ['1', '1', '2'])

    async def test_unconfirmed_commit_exhaustion_blocks_later_writes(self):
        async def process(messages, session, checkpoint=None):
            checkpoint({'turnId': messages[0].uniq_id, 'operations': ['original']})
            return None
        self.provider._process = AsyncMock(side_effect=process)
        first = self.queue.enqueue('a', self.provider, message('第一条', '1'), 's')
        second = self.queue.enqueue('a', self.provider, message('第二条', '2'), 's')
        self.assertEqual((await first).status, 'failed')
        self.assertFalse(second.done())
        self.assertEqual(self.provider._process.await_count, 3)
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs ORDER BY id').fetchone()[0], 'blocked')

    async def test_restart_replays_original_proposal(self):
        gate = asyncio.Event()
        async def interrupted(messages, session, checkpoint=None):
            checkpoint({'turnId': '1', 'operations': ['original']})
            await gate.wait()
        self.provider._process = interrupted
        self.queue.enqueue('a', self.provider, message('第一条', '1'), 's')
        await asyncio.sleep(0)
        self.queue.tasks['a'].cancel()
        await asyncio.gather(self.queue.tasks['a'], return_exceptions=True)
        self.queue.close()
        self.queue = MemoryQueue(self.path, retry_delays=(0, 0))
        async def resumed(messages, session, checkpoint=None):
            self.assertEqual(self.provider._pending_commits[('role', 's', '1')]['operations'], ['original'])
            return SaveResult('committed', (), {'created': 1, 'updated': 0, 'deleted': 0})
        self.provider._process = resumed
        self.queue.bind('a', self.provider)
        await self.queue.tasks['a']
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs').fetchone()[0], 'committed')

    async def test_ordinary_returns_while_explicit_waits_same_queue(self):
        provider = MemoryProvider({})
        provider.role_id = 'role'
        provider._log = Mock()
        gate = asyncio.Event()
        async def process(messages, session, checkpoint=None):
            await gate.wait()
            return SaveResult('committed', ({'action': 'add'},), {'created': 1})
        provider._process = process
        with patch('core.providers.memory.memory_v2.background.get_queue', return_value=self.queue):
            ordinary = await asyncio.wait_for(provider.prepare_chat_memory(message('我喜欢阅读', '1'), 's'), .5)
            self.assertIn('尚未确认', ordinary)
            explicit = asyncio.create_task(provider.prepare_chat_memory(message('请记住我喜欢阅读', '2'), 's'))
            await asyncio.sleep(0)
            self.assertFalse(explicit.done())
            gate.set()
            self.assertIn('新增1条', await explicit)

    async def test_real_chat_entry_reaches_llm_before_memory_finishes(self):
        # Execute the actual method without loading device/audio native libraries.
        import ast, copy, queue, sys, time, uuid
        from core.utils.dialogue import Message, Dialogue, saved_memory_recall_answer
        source = Path(__file__).resolve().parents[1] / 'core/conversation/engine.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == '_chat')
        namespace = dict(asyncio=asyncio, copy=copy, time=time, uuid=uuid, Message=Message,
                         TTSMessageDTO=lambda **kw: kw, SentenceType=SimpleNamespace(FIRST='first'),
                         ContentType=SimpleNamespace(ACTION='action'), TAG='test',
                         saved_memory_recall_answer=saved_memory_recall_answer)
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        gate = asyncio.Event()
        provider = MemoryProvider({})
        provider.role_id = 'role'
        provider.query_memory = AsyncMock(return_value='')
        provider._log = Mock()
        async def save(messages, session, checkpoint=None):
            await gate.wait()
            return SaveResult('skipped')
        provider._process = AsyncMock(side_effect=save)
        conn = SimpleNamespace(memory=provider, loop=asyncio.get_running_loop(), session_id='s',
                               dialogue=Dialogue(), config={}, intent_type='none', logger=Mock(),
                               tts=SimpleNamespace(tts_text_queue=queue.Queue()),
                               llm=SimpleNamespace(response=Mock(side_effect=RuntimeError('stop at model boundary'))))
        with patch.dict(sys.modules, {'core.reminders.conversation': SimpleNamespace(handle_sync=lambda *_: False)}), \
             patch('core.providers.memory.memory_v2.background.get_queue', return_value=self.queue):
            await asyncio.wait_for(asyncio.to_thread(namespace['_chat'], conn, '我喜欢阅读'), 1)
            conn.llm.response.assert_called_once()
            self.assertTrue(any(not task.done() for task in self.queue.tasks.values()))
            gate.set()
            await asyncio.gather(*self.queue.tasks.values())

    async def test_queue_bound_and_second_process_guard(self):
        with self.assertRaisesRegex(RuntimeError, 'already_owned'):
            MemoryQueue(self.path)
        self.queue.max_pending = 1
        self.provider._process = AsyncMock(side_effect=lambda *_args, **_kw: None)
        self.queue.enqueue('a', self.provider, message('第一条', '1'), 's')
        with self.assertRaisesRegex(RuntimeError, 'queue_full'):
            self.queue.enqueue('a', self.provider, message('第二条', '2'), 's')

    def test_filter_only_whole_trivial_messages(self):
        self.assertTrue(trivial('谢谢！'))
        for text in ('谢谢，我今天毕业了', '我不喜欢这个', '继续', '我说错了'):
            self.assertFalse(trivial(text))
        self.assertTrue(needs_confirmation('帮我记一下新的地址'))
        self.assertFalse(needs_confirmation('我今天搬家了'))


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_revision_hit_change_and_explicit_fresh_read(self):
        store = SimpleNamespace(revision=AsyncMock(return_value=1), snapshot=AsyncMock(return_value={'revision': 1, 'memories': []}))
        cache = RecallCache(ttl=20)
        await cache.snapshot(store, 'r')
        await cache.snapshot(store, 'r')
        self.assertEqual(store.snapshot.await_count, 1)
        await cache.snapshot(store, 'r', fresh=True)
        self.assertEqual(store.snapshot.await_count, 1)
        store.revision.return_value = 2
        store.snapshot.return_value = {'revision': 2, 'memories': []}
        await cache.snapshot(store, 'r', fresh=True)
        self.assertEqual(store.snapshot.await_count, 2)
        cache.invalidate()
        await cache.snapshot(store, 'r')
        self.assertEqual(store.snapshot.await_count, 3)

    async def test_history_cached_per_revision(self):
        cache = RecallCache()
        store = SimpleNamespace(history=AsyncMock(return_value=[]))
        view = cache.view(store, {'revision': 1, 'memories': []})
        await view.history('r', 1)
        await view.history('r', 1)
        self.assertEqual(store.history.await_count, 1)
        cache.invalidate()
        await cache.view(store, {'revision': 2, 'memories': []}).history('r', 1)
        self.assertEqual(store.history.await_count, 2)

    async def test_failed_refresh_does_not_return_stale_snapshot(self):
        cache = RecallCache(ttl=0)
        store = SimpleNamespace(revision=AsyncMock(return_value=1), snapshot=AsyncMock(return_value={'revision': 1, 'memories': []}))
        await cache.snapshot(store, 'r')
        store.revision.return_value = None
        store.snapshot.return_value = None
        with self.assertRaises(Exception):
            await cache.snapshot(store, 'r')
