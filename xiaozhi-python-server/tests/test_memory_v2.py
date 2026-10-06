"""固定隔离回归：标准库、脚本化LLM、内存事务；无网络和真实记忆写入。"""
import copy
import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.providers.memory.memory_v2.models import CommitUnconfirmed, ProtocolError, RecallBatch, RetrievalIntent, UserTurn
from core.providers.memory.memory_v2.service import MemoryService
from core.providers.memory.memory_v2.retriever import retrieve
from core.providers.memory.memory_v2.validator import validate
from core.providers.memory.memory_v2.recall import recall
from core.providers.memory.memory_v2.runtime import ModelRuntime

NOW = datetime.fromisoformat("2026-09-17T10:00:00+08:00")


def memory(category="relationship", key="用户的猫数量", content="用户养有三只猫", **extra):
    return dict(category=category, key=key, content=content, **extra)


def request(text, message_id="message-1"):
    return UserTurn.create(text, message_id, NOW, "role", "session")


def update(content, reason="changed", category="relationship", key="用户的猫数量", target=1, **extra):
    return {**dict(action="update", reason=reason, targetId=target, expectedVersion=1,
                   memory=memory(category, key, content)), **extra}


class Store:
    def __init__(self, rows=()):
        self.rows = [{"id": i+1, "version": 1, "status": "active", "source": r["content"],
                      "observedAt": "2026-09-16T10:00:00+08:00", **r} for i, r in enumerate(rows)]
        self.revision, self.commits = 0, []
        self.events, self.receipts = [], {}

    async def snapshot(self, role_id):
        return {"revision": self.revision, "memories": copy.deepcopy(self.rows)}

    async def history(self, role_id, ident):
        return [h for h in self.events if h["memoryId"] == ident]

    async def commit(self, payload):
        if payload["turnId"] in self.receipts:
            return self.receipts[payload["turnId"]]
        if payload["expectedRevision"] != self.revision:
            return None
        rows, events = copy.deepcopy(self.rows), []
        counts = dict(created=0, updated=0, deleted=0)
        for op in payload["operations"]:
            old = next((r for r in rows if r["id"] == op.get("targetId")), None)
            if op["action"] != "add" and (old is None or old["version"] != op["expectedVersion"]):
                return None
            new = {**op["memory"], "source": payload["source"], "observedAt": payload["observedAt"],
                   "id": old["id"] if old else max((r["id"] for r in rows), default=0)+1,
                   "version": old["version"]+1 if old else 1,
                   "status": "invalidated" if op["reason"] == "invalidated" else "closed" if op["reason"] in {"cancelled", "completed"} else old["status"] if old else "active"}
            events.append(dict(memoryId=new["id"], oldJson=json.dumps(old, ensure_ascii=False) if old else None,
                               newJson=json.dumps(new, ensure_ascii=False), reason=op["reason"]))
            if old:
                rows.remove(old)
            rows.append(new)
            counts[{"add": "created", "update": "updated", "delete": "deleted"}[op["action"]]] += 1
        self.rows, self.events = rows, self.events + events
        self.revision += 1
        self.commits.append(payload)
        self.receipts[payload["turnId"]] = counts
        return counts


class V2Tests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, text, rows, operations, category="relationship", keywords=("猫", "宠物"), first_extra=None):
        item = dict(category=category, retrievalKeywords=list(keywords), retrievalQuery="用户相关旧记忆")
        item.update(first_extra or {})
        model = SimpleNamespace(ask=AsyncMock(side_effect=[{"items": [item]}, {"operations": operations}]))
        store = Store(rows)
        result = await MemoryService(model, store).save(request(text))
        self.assertEqual(2, model.ask.await_count)
        self.assertEqual(["retrieval_intent", "decision"], [c.args[0] for c in model.ask.await_args_list])
        return result, store, model

    async def test_01_new_atomic_pet_facts_have_program_source(self):
        text = "我养了三只猫两只狗"
        ops = [dict(action="add", reason="new", memory=memory()),
               dict(action="add", reason="new", memory=memory(key="用户的狗数量", content="用户养有两只狗"))]
        result, store, _ = await self.run_case(text, [], ops)
        self.assertEqual(2, result.counts["created"])
        self.assertEqual({text}, {r["source"] for r in store.rows})
        self.assertEqual({"message-1"}, {r["sourceMessageId"] for r in store.rows})

    async def test_02_changed_uses_old_original_source(self):
        old = memory(source="我现在家里养着三只猫")
        result, store, model = await self.run_case("其中一只送给朋友了，现在只有两只", [old], [update("用户养有两只猫")])
        self.assertEqual("用户养有两只猫", store.rows[0]["content"])
        self.assertEqual(2, store.rows[0]["version"])
        self.assertEqual(old["source"], model.ask.await_args_list[1].args[2]["relatedMemories"][0]["source"])
        self.assertEqual("changed", result.operations[0]["reason"])

    async def test_03_corrected_does_not_recall_wrong_old_value(self):
        _, store, _ = await self.run_case("我之前说错了，其实只有两只", [memory()], [update("用户养有两只猫", "corrected")])
        answer = await recall(store, "role", "我以前养几只猫")
        self.assertIn("两只猫", answer)
        self.assertNotIn("三只猫", answer)
        self.assertEqual("corrected", store.events[0]["reason"])

    async def test_04_country_cancel_can_bind_city_with_different_key(self):
        old = memory("goal", "用户洛杉矶出行计划", "用户计划去美国洛杉矶", source="我准备年底去美国洛杉矶玩几天")
        _, store, _ = await self.run_case("我不计划去美国了", [old],
            [update("用户已取消美国出行计划", "cancelled", "goal", "用户美国出行计划")], "goal", ("美国", "旅行"))
        self.assertEqual((1, "closed"), (store.rows[0]["id"], store.rows[0]["status"]))
        self.assertIn(old["content"], await recall(store, "role", "以前去美国的计划"))

    async def test_05_cancel_and_add_are_one_transaction(self):
        old = memory("goal", "美国计划", "用户计划去美国洛杉矶")
        ops = [update("用户取消美国计划", "cancelled", "goal", "美国计划"),
               dict(action="add", reason="new", memory=memory("goal", "日本计划", "用户计划去日本"))]
        _, store, _ = await self.run_case("我不计划去美国了，我计划去日本", [old], ops, "goal", ("美国", "日本"))
        self.assertEqual(["closed", "active"], [r["status"] for r in store.rows])
        self.assertEqual(1, len(store.commits))

    async def test_06_complete_goal_and_optional_new_event(self):
        old = memory("goal", "六级目标", "用户计划通过六级")
        ops = [update("用户已完成六级目标", "completed", "goal", "六级目标"),
               dict(action="add", reason="new", memory=memory("event", "通过六级经历", "用户已通过六级"))]
        _, store, _ = await self.run_case("我六级已经过了", [old], ops, "goal", ("六级",))
        self.assertEqual([("goal", "closed"), ("event", "active")], [(r["category"], r["status"]) for r in store.rows])

    async def test_07_invalidated_is_logical_and_not_recalled(self):
        op = dict(action="update", reason="invalidated", targetId=1, expectedVersion=1)
        _, store, _ = await self.run_case("你记错了，我从来没有养过狗", [memory(key="狗饲养", content="用户养有一只狗")], [op], keywords=("狗",))
        self.assertEqual("invalidated", store.rows[0]["status"])
        self.assertEqual(1, len(store.events))
        self.assertNotIn("一只狗", await recall(store, "role", "我养过狗吗"))

    async def test_08_empty_database_allows_add(self):
        op = dict(action="add", reason="new", memory=memory("preference", "摄影偏好", "用户喜欢摄影"))
        result, _, _ = await self.run_case("我喜欢摄影", [], [op], "preference", ("摄影",))
        self.assertEqual("committed", result.status)

    async def test_first_stage_cannot_send_hints_to_second(self):
        result, _, model = await self.run_case("我喜欢摄影", [], [], first_extra={"intent_hint": "completed", "content": "污染内容"})
        decision_input = model.ask.await_args_list[1].args[2]
        self.assertEqual("skipped", result.status)
        self.assertNotIn("intent_hint", json.dumps(decision_input))
        self.assertNotIn("污染内容", json.dumps(decision_input, ensure_ascii=False))

    async def test_no_memory_value_stops_after_first_call(self):
        model = SimpleNamespace(ask=AsyncMock(return_value={"items": []}))
        store = SimpleNamespace(snapshot=AsyncMock(), commit=AsyncMock())
        result = await MemoryService(model, store).save(request("今天天气怎么样"))
        self.assertEqual("skipped", result.status)
        self.assertEqual(1, model.ask.await_count)
        store.snapshot.assert_not_called()

    def test_unseen_target_and_version_rejected_before_commit(self):
        row = {"id": 1, "version": 3, "status": "active", "source": "我养三只猫", **memory()}
        for batch, op in ((RecallBatch(0, (), (row,), 1), update("两只猫", expectedVersion=3)),
                          (RecallBatch(0, (row,), (row,), 0), update("两只猫"))):
            with self.assertRaises(ProtocolError):
                validate({"operations": [op]}, request("现在两只猫"), batch)

    async def test_uncertain_does_not_partially_apply(self):
        result, store, _ = await self.run_case("也许两只猫", [memory()], [update("用户养两只猫"), {"action": "uncertain"}])
        self.assertEqual("uncertain", result.status)
        self.assertEqual([], store.commits)

    async def test_sources_from_model_cannot_override_application(self):
        op = dict(action="add", reason="new", memory=memory(source="伪造来源", observedAt="1900年", sourceMessageId="假的"))
        _, store, _ = await self.run_case("我养三只猫", [], [op])
        self.assertEqual("我养三只猫", store.rows[0]["source"])
        self.assertEqual("message-1", store.rows[0]["sourceMessageId"])
        self.assertEqual(request("我养三只猫").observed_at, store.rows[0]["observedAt"])

    async def test_retriever_returns_sources_and_never_binds_by_key(self):
        store = Store([memory(source="我的三只猫"), memory(content="朋友养有三只猫", source="朋友有三只猫")])
        batch = await retrieve(store, "role", (RetrievalIntent(("猫",), "猫数量"),))
        self.assertEqual({1, 2}, {r["id"] for r in batch.records})
        self.assertTrue(all(r["source"] for r in batch.records))

    async def test_commit_conflict_never_reports_success_or_retries(self):
        op = dict(action="add", reason="new", memory=memory())
        model = SimpleNamespace(ask=AsyncMock(side_effect=[{"items": [{"category": "relationship", "retrievalKeywords": ["猫"], "retrievalQuery": "猫"}]}, {"operations": [op]}]))
        store = Store()
        store.commit = AsyncMock(return_value=None)
        with self.assertRaisesRegex(CommitUnconfirmed, "commit_unconfirmed"):
            await MemoryService(model, store).save(request("我养三只猫"))
        self.assertEqual(1, store.commit.await_count)
        self.assertEqual(2, model.ask.await_count)

    async def test_malformed_json_is_not_repaired_or_retried(self):
        llm = SimpleNamespace(response_no_stream=Mock(return_value='{"items":'))
        log = Mock()
        with self.assertRaisesRegex(ProtocolError, "model_invalid_json"):
            await ModelRuntime(llm, {}, log).ask("retrieval_intent", "prompt", {}, 100)
        self.assertEqual(1, llm.response_no_stream.call_count)
        self.assertTrue(any(c.args[0] == "retrieval_intent_raw_output" for c in log.call_args_list))

    def test_invalid_lifecycle_duplicate_target_and_boolean_id_are_rejected(self):
        active = {"id": 1, "version": 1, "status": "active", "source": "猫三只", **memory()}
        batch = RecallBatch(0, (active,), (active,), 0)
        cases = [[update("猫两只", reason="cancelled")], [update("猫两只"), update("猫一只")],
                 [update("猫两只", targetId=True)], [update("猫两只", expectedVersion=True)]]
        for operations in cases:
            with self.subTest(operations=operations), self.assertRaises(ProtocolError):
                validate({"operations": operations}, request("我现在养两只猫"), batch)

    def test_note_needs_explicit_flag_and_duplicate_add_is_rejected(self):
        active = {"id": 1, "version": 1, "status": "active", "source": "猫三只", **memory()}
        for new in (memory(key="另一个措辞"), memory("note", "事项", "用户事项")):
            with self.assertRaises(ProtocolError):
                validate({"operations": [dict(action="add", reason="new", memory=new)]},
                         request("我养三只猫"), RecallBatch(0, (active,), (active,), 0))

    async def test_provider_feedback_and_background_save_share_two_calls(self):
        # 仅替换应用日志依赖，避免读取运行配置或创建日志文件；实际入口类仍直接运行。
        import importlib
        import sys
        fake_logger = SimpleNamespace(setup_logging=lambda: Mock())
        with patch.dict(sys.modules, {"config.logger": fake_logger}):
            module = importlib.import_module("core.providers.memory.memory_v2.memory_v2")
        provider = module.MemoryProvider({})
        provider.store = Store()
        llm = SimpleNamespace(response_no_stream=Mock(side_effect=[
            json.dumps({"items": [{"category": "relationship", "retrievalKeywords": ["猫"], "retrievalQuery": "猫数量"}]}),
            json.dumps({"operations": [dict(action="add", reason="new", memory=memory())]})]))
        provider.init_memory("role", llm, session_id="session")
        message = SimpleNamespace(role="user", content="我养三只猫", uniq_id="msg", created_at=NOW)
        feedback = await provider.prepare_memory_feedback([message], "session")
        await provider.save_memory([message], "session")
        self.assertIn("新增1条", feedback)
        self.assertEqual(2, llm.response_no_stream.call_count)
        self.assertEqual(1, len(provider.store.commits))

    def test_old_configuration_name_routes_to_v2_without_old_import(self):
        import importlib
        import sys
        with patch.dict(sys.modules, {"config.logger": SimpleNamespace(setup_logging=lambda: Mock())}):
            factory = importlib.import_module("core.utils.memory")
            with patch.object(factory.os.path, "exists", return_value=True):
                instance = factory.create_instance("mem_local_short", {})
        self.assertEqual("core.providers.memory.memory_v2.memory_v2", type(instance).__module__)

    def test_v2_has_no_import_of_frozen_pipeline(self):
        import ast
        from core.providers.memory import memory_v2
        for path in Path(memory_v2.__file__).parent.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("mem_local_short", node.module or "")
                elif isinstance(node, ast.Import):
                    self.assertTrue(all("mem_local_short" not in n.name for n in node.names))


if __name__ == "__main__":
    unittest.main()
