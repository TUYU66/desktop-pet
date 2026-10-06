"""执行边界回归：仅模拟模型与后端，无真实服务调用。"""
import copy
import importlib
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from test_memory_v2 import Store, memory, NOW
from core.providers.memory.memory_v2.models import CommitConflict, StoreUnavailable, RetrievalIntent
from core.providers.memory.memory_v2.recall import recall
from core.providers.memory.memory_v2.retriever import read_snapshot, retrieve


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, store=None, operations=None):
        with patch.dict(sys.modules, {"config.logger": SimpleNamespace(setup_logging=lambda: Mock())}):
            module = importlib.import_module("core.providers.memory.memory_v2.memory_v2")
        provider = module.MemoryProvider({})
        provider.store = store or Store()
        responses = [
            {"items": [{"retrievalKeywords": ["猫"], "retrievalQuery": "猫数量"}]},
            {"operations": operations if operations is not None else [dict(action="add", reason="new", memory=memory())]},
        ]
        provider.init_memory("role", SimpleNamespace(response_no_stream=Mock(
            side_effect=[json.dumps(r, ensure_ascii=False) for r in responses])), session_id="session")
        message = SimpleNamespace(role="user", content="我养三只猫", uniq_id="msg", created_at=NOW)
        return provider, [message]

    async def test_snapshot_unavailable_is_retryable_not_rejected(self):
        provider, messages = self.provider()
        actual = provider.store.snapshot
        provider.store.snapshot = AsyncMock(side_effect=[None, await actual("role")])
        # 第一次仅做检索意图，第二次从检索意图开始恢复。
        answers = provider.llm.response_no_stream.side_effect
        intent, decision = list(answers)
        provider.llm.response_no_stream.side_effect = [intent, intent, decision]
        first = await provider.prepare_memory_feedback(messages, "session")
        self.assertIn("尚未确认", first)
        self.assertFalse(provider._receipts)
        self.assertFalse(provider._pending_commits)
        second = await provider.prepare_memory_feedback(messages, "session")
        self.assertIn("新增1条", second)
        self.assertEqual(1, len(provider.store.commits))

    def test_malformed_snapshot_is_store_failure(self):
        for snapshot in (None, {}, {"revision": True, "memories": []},
                         {"revision": 0, "memories": [{}]}):
            with self.subTest(snapshot=snapshot), self.assertRaises(StoreUnavailable):
                read_snapshot(snapshot)

    async def test_lost_response_retries_identical_commit_without_model(self):
        provider, messages = self.provider()
        actual = provider.store.commit
        payloads = []

        async def lost_response(payload):
            payloads.append(copy.deepcopy(payload))
            result = await actual(payload)
            if len(payloads) == 1:
                raise TimeoutError("response lost after commit")
            return result

        provider.store.commit = lost_response
        first = await provider.prepare_memory_feedback(messages, "session")
        self.assertIn("尚未确认", first)
        self.assertNotIn("没有被修改", first)
        self.assertEqual(1, len(provider._pending_commits))
        second = await provider.prepare_memory_feedback(messages, "session")
        await provider.save_memory(messages, "session")
        self.assertIn("新增1条", second)
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(2, provider.llm.response_no_stream.call_count)
        self.assertEqual(1, len(provider.store.rows))
        self.assertEqual(1, len(provider.store.commits))
        self.assertFalse(provider._pending_commits)

    async def test_empty_commit_response_keeps_original_proposal(self):
        provider, messages = self.provider()
        provider.store.commit = AsyncMock(side_effect=[None, {"created": 1, "updated": 0, "deleted": 0}])
        await provider.save_memory(messages, "session")
        result = await provider.save_memory(messages, "session")
        self.assertEqual(1, len(result))
        calls = provider.store.commit.await_args_list
        self.assertEqual(calls[0].args, calls[1].args)
        self.assertEqual(2, provider.llm.response_no_stream.call_count)

    async def test_conflict_is_not_replanned_or_force_written(self):
        provider, messages = self.provider()
        provider.store.commit = AsyncMock(side_effect=CommitConflict("commit_conflict"))
        first = await provider.prepare_memory_feedback(messages, "session")
        second = await provider.prepare_memory_feedback(messages, "session")
        self.assertIn("冲突", first)
        self.assertEqual(first, second)
        self.assertEqual(1, provider.store.commit.await_count)
        self.assertEqual(2, provider.llm.response_no_stream.call_count)
        self.assertFalse(provider._pending_commits)

    async def test_validator_rejection_is_cached(self):
        provider, messages = self.provider(operations=[dict(action="add", reason="new", memory=memory(category="invalid"))])
        first = await provider.prepare_memory_feedback(messages, "session")
        await provider.save_memory(messages, "session")
        self.assertIn("未通过安全校验", first)
        self.assertEqual(2, provider.llm.response_no_stream.call_count)
        self.assertEqual([], provider.store.commits)

    async def test_short_questions_recall_single_character_subjects(self):
        store = Store([memory(source="我养了三只猫"),
                       memory(key="用户的车颜色", content="用户的车是蓝色", source="我的车是蓝色")])
        for query, wanted, unwanted in [("猫有几只？", "三只猫", "蓝色"),
                                        ("车呢？", "蓝色", "三只猫")]:
            with self.subTest(query=query):
                answer = await recall(store, "role", query)
                self.assertIn(wanted, answer)
                self.assertNotIn(unwanted, answer)
        self.assertEqual("没有检索到相关长期记忆。", await recall(store, "role", "钢琴呢？"))

    async def test_write_retrieval_still_allows_empty_candidates(self):
        batch = await retrieve(Store([memory()]), "role", (RetrievalIntent(("钢琴",), "钢琴演奏"),))
        self.assertFalse(batch.records)

    async def test_api_conflicts_and_timeouts_reach_store_with_distinct_types(self):
        import httpx
        with patch.dict(sys.modules, {"config.logger": SimpleNamespace(setup_logging=lambda: Mock())}):
            api = importlib.import_module("config.manage_api_client")
        from core.providers.memory.memory_v2.store import JavaMemoryStore
        for status, body in [(409, {}), (200, {"code": 409, "msg": "private detail"})]:
            client = AsyncMock()
            client.post.return_value = httpx.Response(status, json=body, request=httpx.Request("POST", "http://localhost/test"))
            manager = AsyncMock()
            manager.__aenter__.return_value = client
            with self.subTest(status=status), patch.object(api.httpx, "AsyncClient", return_value=manager):
                with self.assertRaises(CommitConflict):
                    await JavaMemoryStore().commit({})
        client.post.side_effect = httpx.ReadTimeout("response lost")
        with patch.object(api.httpx, "AsyncClient", return_value=manager):
            with self.assertRaises(httpx.ReadTimeout):
                await JavaMemoryStore().commit({})
            # 未启用 strict 的既有调用保持原协议。
            self.assertIsNone(await api.semantic_memory_request("commit", {}))


if __name__ == "__main__":
    unittest.main()
