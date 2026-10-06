"""隔离验证语义决策的执行边界；不连接真实模型和数据库。"""
import copy
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from core.providers.memory.mem_local_short import semantic as s
from semantic_memory_fixture import MemoryFixture

WHEN = datetime.fromisoformat("2026-09-16T10:30:00+08:00")


def provider(*answers):
    return SimpleNamespace(role_id="test", _processed=[], _feedback_counts=None,
                           _diagnose=Mock(), _ask_json=AsyncMock(side_effect=answers))


def extracted(key="用户的猫数量", content="用户养有三只猫", category="relationship", **extra):
    return dict(key=key, content=content, category=category, **extra)


def candidate(memory, text, hint="assert", **extra):
    return {**memory, "source": text, "intent_hint": hint, **extra}


def decision(memory, action="UPDATE", reason="changed", target=1, index=0, **extra):
    op = dict(action=action, reason=reason, memory=memory, candidateIndex=index, needsAudit=False)
    if action != "ADD":
        op.update(targetId=target, expectedVersion=1, sameTarget=True)
    return {**op, **extra}


class SemanticTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, p, db, text="我养有三只猫", turn="t"):
        with patch.object(s, "semantic_memory_snapshot", db.snapshot), patch.object(s, "semantic_memory_request", db.request):
            return await s.save_turn(p, text, WHEN, turn, "session", [])

    async def test_pet_facts_without_model_source_use_original_turn(self):
        text = "我养了三只猫两只狗"
        cat = extracted()
        dog = extracted("用户的狗数量", "用户养有两只狗", source="")
        p = provider({"memories": [cat, dog]}, {"operations": [
            decision(cat, "ADD", "new"), decision(dog, "ADD", "new", index=1)]}, {"errors": []})
        db = MemoryFixture()
        ops = await self.run_turn(p, db, text)
        self.assertEqual(2, len(ops))
        self.assertEqual({text}, {op["memory"]["source"] for op in ops})
        self.assertEqual("extract", p._ask_json.await_args_list[0].kwargs["diagnostic"])

    def test_missing_source_fallback_never_rewrites_explicit_evidence(self):
        text = "我养了三只猫两只狗"
        for source in (None, "", "  "):
            self.assertEqual(text, s.parse_memory(extracted(source=source), text, WHEN.isoformat())[0]["source"])
        with self.assertRaisesRegex(ValueError, "source_reference_not_substring"):
            s.parse_memory(extracted(source="我养了两只狗"), text, WHEN.isoformat())
        with self.assertRaisesRegex(ValueError, "source_reference_invalid_type"):
            s.parse_memory(extracted(source=[text]), text, WHEN.isoformat())
        with self.assertRaisesRegex(ValueError, "note_requires_explicit_source"):
            s.parse_memory(extracted("事项", "需要记住的事项", "note"), text, WHEN.isoformat())

    async def test_invalid_extraction_source_logs_candidate_before_stopping(self):
        text = "我养了三只猫两只狗"
        raw = extracted(source="我养了两只狗")
        p = provider({"memories": [raw]})
        db = MemoryFixture()
        self.assertIsNone(await self.run_turn(p, db, text))
        detail = next(json.loads(c.args[1]) for c in p._diagnose.call_args_list if c.args[0] == "semantic_candidate_invalid")
        self.assertEqual(0, detail["candidateIndex"])
        self.assertEqual(raw["source"], detail["candidate"]["source"])
        self.assertEqual(text, detail["latestUser"])
        self.assertEqual([], db.history_rows)

    async def test_case1_absolute_update_without_extra_audit(self):
        text = "我现在只养两只猫"
        new = candidate(extracted(content="用户养有两只猫"), text, "changed")
        p = provider({"memories": [new]}, {"operations": [decision(new)]})
        db = MemoryFixture([extracted()])
        ops = await self.run_turn(p, db, text)
        self.assertEqual((1, "changed", "用户养有两只猫"), (ops[0]["targetId"], ops[0]["reason"], db.rows[0]["content"]))
        self.assertEqual(2, db.rows[0]["version"])
        self.assertEqual(2, p._ask_json.await_count)
        self.assertEqual(WHEN.astimezone().isoformat(timespec="seconds"), db.rows[0]["observedAt"])

    async def test_case2_pronoun_correction_is_not_true_old_history(self):
        text = "我说错了，其实只有两只"
        hint = candidate(extracted(content="用户其实只有两只"), text, "uncertain", needsContext=True)
        new = candidate(extracted(content="用户养有两只猫"), text)
        p = provider({"memories": [hint]}, {"operations": [decision(new, reason="corrected")]}, {"errors": []})
        db = MemoryFixture([extracted()])
        ops = await self.run_turn(p, db, text)
        self.assertEqual("corrected", ops[0]["reason"])
        p._ask_json = AsyncMock(return_value={"ids": [1]})
        with patch.object(s,"semantic_memory_snapshot",db.snapshot), patch.object(s,"semantic_memory_history",db.history):
            result = await s.recall(p,"我以前有几只猫？")
        self.assertIn("两只", result)
        self.assertNotIn("三只", result)

    async def test_case3_different_topic_name_can_cancel_city_goal(self):
        text = "我不计划去美国了"
        old = extracted("用户洛杉矶出行计划", "用户计划去美国洛杉矶", "goal")
        new = candidate(extracted("用户美国出行计划", "用户不计划去美国了", "goal"), text, "cancelled")
        p = provider({"memories": [new]}, {"operations": [decision(new, "CANCEL", "cancelled")]})
        db = MemoryFixture([old])
        await self.run_turn(p, db, text)
        self.assertEqual((1, "closed", new["key"]), (db.rows[0]["id"], db.rows[0]["status"], db.rows[0]["key"]))
        self.assertIn(old["content"], db.history_rows[0]["oldJson"])
        self.assertEqual(1, p._ask_json.await_args_list[1].args[1]["relatedMemories"][0]["id"])

    async def test_case4_cancel_and_add_commit_together(self):
        text = "我不计划去美国了，我计划去日本"
        old = extracted("用户洛杉矶出行计划", "用户计划去美国洛杉矶", "goal")
        cancel = candidate(extracted("用户美国出行计划", "用户不计划去美国了", "goal"), text, "cancelled")
        japan = candidate(extracted("用户日本出行计划", "用户计划去日本", "goal"), text)
        p = provider({"memories": [cancel, japan]}, {"operations": [decision(cancel,"CANCEL","cancelled"),
                     decision(japan,"ADD","new",index=1)]}, {"errors": []})
        db = MemoryFixture([old])
        ops = await self.run_turn(p, db, text)
        self.assertEqual(["cancelled", "new"], [o["reason"] for o in ops])
        self.assertEqual(["closed", "active"], [r["status"] for r in db.rows])
        self.assertEqual(1, db.revision)

    async def test_case5_completion_retains_goal_and_creates_event(self):
        text = "我六级过了"
        old = extracted("用户六级目标", "用户计划通过六级", "goal")
        new = candidate(extracted("用户六级目标", "用户已完成六级目标", "goal"), text, "completed")
        event = candidate(extracted("用户通过六级经历", "用户已经通过六级", "event"), text)
        p = provider({"memories": [new]}, {"operations": [decision(new,"COMPLETE","completed",event=event)]}, {"errors": []})
        db = MemoryFixture([old])
        ops = await self.run_turn(p, db, text)
        self.assertEqual(2,len(ops))
        self.assertEqual([(1,"goal","closed"),(2,"event","active")], [(r["id"],r["category"],r["status"]) for r in db.rows])
        self.assertEqual(["completed","new"], [h["reason"] for h in db.history_rows])

    async def test_case6_never_owned_dog_invalidates_without_true_history(self):
        text = "你记错了，我从来没有养过狗"
        old = extracted("用户狗饲养情况", "用户养有一只狗")
        new = candidate(extracted("用户狗饲养情况", "用户从来没有养过狗"), text, "invalidated")
        p = provider({"memories": [new]}, {"operations": [decision(new,"INVALIDATE","invalidated",source=text)]}, {"errors": []})
        db = MemoryFixture([old])
        await self.run_turn(p, db, text)
        self.assertEqual("invalidated", db.rows[0]["status"])
        self.assertEqual(1,len(db.history_rows))
        with patch.object(s,"semantic_memory_snapshot",db.snapshot):
            self.assertNotIn("一只狗", await s.recall(p,"我养过狗吗？",list_all=True))

    async def test_case7_missing_target_allows_simple_add(self):
        text = "我计划去日本"
        new = candidate(extracted("用户日本出行计划","用户计划去日本","goal"),text)
        p = provider({"memories":[new]}, {"operations":[decision(new,"ADD","new")]})
        db = MemoryFixture()
        self.assertEqual("add", (await self.run_turn(p,db,text))[0]["action"])
        self.assertEqual(2,p._ask_json.await_count)

    async def test_empty_extraction_still_reaches_decision(self):
        text="我不计划去美国了"
        new=candidate(extracted("用户美国出行计划", "用户取消美国计划", "goal"),text)
        p=provider({"memories":[]},{"operations":[decision(new,"CANCEL","cancelled",index=None)]},{"errors":[]})
        db=MemoryFixture([extracted("用户洛杉矶计划","用户计划去美国洛杉矶","goal")])
        self.assertEqual("cancelled",(await self.run_turn(p,db,text))[0]["reason"])

    async def test_same_key_does_not_force_target_binding(self):
        text="朋友养有两只猫"
        new=candidate(extracted(content="朋友养有两只猫"),text)
        p=provider({"memories":[new]},{"operations":[{"action":"UNCERTAIN","explanation":"主题归属不清"}]})
        db=MemoryFixture([extracted()])
        self.assertIsNone(await self.run_turn(p,db,text))
        self.assertEqual([],db.history_rows)

    async def test_unseen_id_or_wrong_version_rejects_whole_plan(self):
        text="我现在养两只猫"
        new=candidate(extracted(content="用户养有两只猫"),text)
        for extra in ({"targetId":99},{"expectedVersion":2},{"targetId":True},{"sameTarget":False}):
            with self.subTest(extra=extra):
                p=provider({"memories":[new]},{"operations":[decision(new,**extra)]})
                db=MemoryFixture([extracted()])
                self.assertIsNone(await self.run_turn(p,db,text))
                self.assertEqual([],db.history_rows)

    def test_database_id_not_shown_to_model_cannot_be_updated(self):
        text="我现在养两只猫"
        new=candidate(extracted(content="用户养有两只猫"),text)
        parsed=s.parse_candidate(new,text,WHEN.isoformat())
        db=MemoryFixture([extracted()])
        with self.assertRaisesRegex(ValueError,"target_not_in_related"):
            s.validate_plan({"operations":[decision(new)]},[parsed],[],db.rows,text,WHEN.isoformat())

    def test_retrieval_keeps_possible_targets_without_binding_exact_key(self):
        text="我现在只养两只猫"
        new=candidate(extracted(content="用户养有两只猫"),text)
        parsed=s.parse_candidate(new,text,WHEN.isoformat())
        db=MemoryFixture([extracted(),extracted(content="朋友养有三只猫"),
                          extracted("用户狗饲养情况","用户养有两只狗")])
        related,omitted=s.retrieve_related(db.rows,[parsed],text)
        self.assertEqual({1,2,3},{r["id"] for r in related})
        self.assertEqual(0,omitted)
        self.assertTrue(all("version" in r for r in related))

    async def test_obvious_duplicate_add_is_blocked_even_with_new_key(self):
        text="我养有三只猫"
        new=candidate(extracted("用户养猫数量"),text)
        p=provider({"memories":[new]},{"operations":[decision(new,"ADD","new")]})
        db=MemoryFixture([extracted()])
        self.assertIsNone(await self.run_turn(p,db,text))
        self.assertEqual(1,len(db.rows))
        self.assertEqual([],db.history_rows)

    async def test_closed_goal_cannot_be_cancelled_again(self):
        text="我不计划去美国了"
        new=candidate(extracted("美国计划","用户取消美国计划","goal"),text)
        p=provider({"memories":[new]},{"operations":[decision(new,"CANCEL","cancelled")]})
        db=MemoryFixture([{**extracted("美国计划","用户取消美国计划","goal"),"status":"closed"}])
        self.assertIsNone(await self.run_turn(p,db,text))
        self.assertEqual([],db.history_rows)

    async def test_plan_reverting_to_old_value_requires_audit(self):
        text="我现在只养两只猫"
        new=candidate(extracted(content="用户养有两只猫"),text)
        wrong=candidate(extracted(),text)
        p=provider({"memories":[new]},{"operations":[decision(wrong)]},{"errors":["原话两只，提案三只"]})
        db=MemoryFixture([extracted()])
        self.assertIsNone(await self.run_turn(p,db,text))
        self.assertEqual([],db.history_rows)
        self.assertEqual(s.RISK_AUDIT,p._ask_json.await_args_list[-1].args[0])

    async def test_relative_quantity_is_checked_before_audit(self):
        text="我又养了一只猫"
        hint=candidate(extracted(content="用户又养了一只猫"),text,"relative")
        for total in ("四","五"):
            with self.subTest(total=total):
                new=candidate(extracted(content=f"用户养有{total}只猫"),text)
                p=provider({"memories":[hint]},{"operations":[decision(new,quantity={"before":"三只","after":f"{total}只","delta":1})]},{"errors":[]})
                db=MemoryFixture([extracted()])
                ops=await self.run_turn(p,db,text)
                if total=="四": self.assertEqual("用户养有四只猫",db.rows[0]["content"])
                else:
                    self.assertIsNone(ops)
                    self.assertEqual([],db.history_rows)

    async def test_completion_without_event_cannot_partially_close_goal(self):
        text="我六级过了"
        new=candidate(extracted("用户六级目标","用户完成六级目标","goal"),text)
        p=provider({"memories":[new]},{"operations":[decision(new,"COMPLETE","completed")]})
        db=MemoryFixture([extracted("用户六级目标","用户计划通过六级","goal")])
        self.assertIsNone(await self.run_turn(p,db,text))
        self.assertEqual("active",db.rows[0]["status"])

    async def test_invalid_second_operation_does_not_commit_first(self):
        text="我不去美国了，我计划去日本"
        new=candidate(extracted("美国计划","用户取消美国计划","goal"),text)
        p=provider({"memories":[new]},{"operations":[decision(new,"CANCEL","cancelled"),decision(new,target=99)]})
        db=MemoryFixture([extracted("美国计划","用户计划去美国","goal")])
        self.assertIsNone(await self.run_turn(p,db,text))
        self.assertEqual("active",db.rows[0]["status"])

    async def test_missing_snapshot_never_assumes_no_targets(self):
        new=candidate(extracted(),"我养有三只猫")
        p=provider({"memories":[new]})
        with patch.object(s,"semantic_memory_snapshot",AsyncMock(return_value=None)), patch.object(s,"semantic_memory_request",AsyncMock()) as commit:
            self.assertIsNone(await s.save_turn(p,"我养有三只猫",WHEN,"t","s",[]))
            commit.assert_not_called()

    def test_source_and_note_authorization_are_enforced(self):
        for raw,text in ((candidate(extracted(),"我有四只猫"),"我有三只猫"),
                         (candidate(extracted("临时事项","口令纸鹤","note"),"临时口令是纸鹤"),"请记住我喜欢摄影，临时口令是纸鹤")):
            with self.assertRaises(ValueError): s.parse_candidate(raw,text,WHEN.isoformat())

    async def test_pagination_keeps_revision_and_loads_later_targets(self):
        from config import manage_api_client as api
        pages=[dict(schemaVersion=3,writeProtocolVersion=4,roleId="test",revision=8,memories=[{"id":1}],hasMore=True,nextId=1),
               dict(schemaVersion=3,writeProtocolVersion=4,roleId="test",revision=8,memories=[{"id":2}],hasMore=False,nextId=2)]
        with patch.object(api,"semantic_memory_request",AsyncMock(side_effect=pages)) as request:
            result=await api.semantic_memory_snapshot("test")
        self.assertEqual([1,2],[r["id"] for r in result["memories"]])
        self.assertEqual(8,request.await_args_list[1].args[1]["expectedRevision"])

    async def test_changed_revision_between_pages_fails_closed(self):
        from config import manage_api_client as api
        pages=[dict(schemaVersion=3,writeProtocolVersion=4,roleId="test",revision=8,memories=[{"id":1}],hasMore=True,nextId=1),
               dict(schemaVersion=3,writeProtocolVersion=4,roleId="test",revision=9,memories=[],hasMore=False,nextId=1)]
        with patch.object(api,"semantic_memory_request",AsyncMock(side_effect=pages)):
            self.assertIsNone(await api.semantic_memory_snapshot("test"))

    async def test_invalidated_memory_is_excluded_from_recall(self):
        p=provider()
        db=MemoryFixture([{**extracted(),"status":"invalidated"}])
        with patch.object(s,"semantic_memory_snapshot",db.snapshot):
            result=await s.recall(p,"我养几只猫？")
        self.assertNotIn("三只",result)
        p._ask_json.assert_not_called()

    async def test_audit_logs_raw_and_parsed_without_error_mapping(self):
        from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
        p=MemoryProvider({"structured_output":"text"})
        raw='```json\n{"errors":["数量不符"]}\n```'
        p.llm=SimpleNamespace(response_no_stream=Mock(return_value=raw))
        p._diagnose=Mock()
        result=await p._ask_json(s.RISK_AUDIT,{"trace":"turn=t; candidate=0"},diagnostic="audit")
        self.assertEqual({"errors":["数量不符"]},result)
        logs={call.args[0]:call.args[1] for call in p._diagnose.call_args_list}
        import json
        self.assertEqual(raw,json.loads(logs["audit_raw_output"])["raw"])
        self.assertEqual(result,json.loads(logs["audit_parsed_result"])["parsed"])
        self.assertIn("audit_input",logs)

    async def test_invalid_audit_json_still_has_raw_log(self):
        from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
        p=MemoryProvider({"structured_output":"text"})
        p.llm=SimpleNamespace(response_no_stream=Mock(return_value='{"errors":'))
        p._diagnose=Mock()
        with self.assertRaises(ValueError):
            await p._ask_json(s.RISK_AUDIT,{"trace":"t"},diagnostic="audit")
        phases=[call.args[0] for call in p._diagnose.call_args_list]
        self.assertIn("audit_raw_output",phases)
        self.assertIn("audit_parse_error",phases)


if __name__ == "__main__":
    unittest.main()
