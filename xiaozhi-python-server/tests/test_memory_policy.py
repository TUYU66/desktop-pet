"""默认受控记忆策略回归；模型、HTTP 和真实记忆均隔离。"""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from test_memory import Llm, MODULE, WHEN, review, fact, memory
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
from core.providers.memory.mem_local_short.policy import explicit_request


def candidate(field, value, **extra):
    return dict(field=field, value=value, sourceId=0, intent="assert", temporalScope="current", **extra)


def record(op, id=11):
    return dict(id=id, category=op["category"], version=2,
                factJson=json.dumps(op["fact"], ensure_ascii=False), content="测试记录")


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_goal_retains_stated_deadline_as_current_intention(self):
        ops,_,_=await self.run_turn("我准备半年内考过六级",[candidate("goal","半年内考过六级",topic="六级")])
        self.assertEqual("goal",ops[0]["category"])
        self.assertEqual("current",ops[0]["fact"]["temporalScope"])
        self.assertEqual("半年内考过六级",ops[0]["fact"]["value"])

    async def test_habits_and_goals_have_separate_topic_slots(self):
        ops,_,_=await self.run_turn("我每天晚上跑步，平时早上喝咖啡，也准备半年内考过六级",[
            candidate("habit","每天晚上跑步",topic="跑步"),
            candidate("habit","平时早上喝咖啡",topic="喝咖啡"),
            candidate("goal","半年内考过六级",topic="六级")])
        self.assertEqual(3,len({op["fact"]["predicateId"] for op in ops}))

    async def test_two_named_pets_share_attributes_but_not_identity(self):
        entries=[]
        for name in ("豆包","雪球"):
            entries.extend([candidate("pet_name",name,entityName=name),
                            candidate("pet_species","猫",entityName=name),
                            candidate("pet_relation","用户饲养的宠物",entityName=name)])
        ops,_,_=await self.run_turn("我有两只猫，一只叫豆包，一只叫雪球",entries)
        self.assertEqual(6,len(ops))
        ids={name:{op["fact"]["entityId"] for op in ops if op["fact"]["subject"]=="宠物："+name} for name in ("豆包","雪球")}
        self.assertEqual(1,len(ids["豆包"])); self.assertEqual(1,len(ids["雪球"]))
        self.assertNotEqual(ids["豆包"],ids["雪球"])

    async def test_named_pet_repetition_is_deduplicated(self):
        entry=candidate("pet_species","猫",entityName="豆包")
        ops,_,_=await self.run_turn("我养的猫叫豆包",[entry])
        repeated,_,writer=await self.run_turn("豆包是我养的猫",[entry],[record(ops[0])])
        self.assertEqual([],repeated); writer.assert_not_awaited()

    async def test_pet_cannot_use_absent_name_generic_subject_or_swapped_name(self):
        for entry in (candidate("pet_species","猫",entityName="雪球"),
                      candidate("pet_species","猫",entityName="猫"),
                      candidate("pet_name","雪球",entityName="豆包")):
            ops,_,writer=await self.run_turn("我养的猫叫豆包",[entry])
            self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_expanded_fields_still_require_semantic_approval(self):
        # Mock rejection tests the enforcement boundary, not the real model's accuracy.
        cases=[("今天晚上去跑步",candidate("habit","每天晚上跑步",topic="跑步")),
               ("我并不是每天晚上跑步",candidate("habit","每天晚上跑步",topic="跑步")),
               ("我等下想打游戏",candidate("goal","打游戏",topic="打游戏")),
               ("如果以后我养猫，我想叫它豆包",candidate("pet_name","豆包",entityName="豆包")),
               ("朋友的猫叫豆包",candidate("pet_relation","用户饲养的宠物",entityName="豆包"))]
        for text,entry in cases:
            ops,_,writer=await self.run_turn(text,[entry],approved=False)
            self.assertEqual([],ops,text); writer.assert_not_awaited()

    async def run_turn(self, text, candidates, memories=(), approved=True, context=()):
        llm=Llm({"facts":candidates}, *[review(sourceSupported=approved) for _ in candidates])
        provider=MemoryProvider({}); provider.init_memory("default", llm)
        loader=AsyncMock(return_value=dict(schemaVersion=2, retrievalVersion=1, writeProtocolVersion=3, roleId="default", revision=5, memories=list(memories)))
        writer=AsyncMock(return_value=dict(created=1,updated=0,deleted=0))
        with patch(MODULE+".search_memory_facts",loader), patch(MODULE+".commit_memory_facts",writer):
            result=await provider.save_memory([NS(role="user",content=text,created_at=WHEN,uniq_id="turn")],
                                              "session",user_context=context)
        return result,llm,writer

    async def test_age_and_student_are_separate_fixed_fields(self):
        ops,llm,writer=await self.run_turn("我是大学生今年21岁",
            [candidate("age","21岁",category="relationship"),candidate("student_status","大学生")])
        self.assertEqual({"年龄","学籍"},{op["fact"]["predicate"] for op in ops})
        self.assertTrue(all(op["category"]=="profile" and op["fact"]["memoryMode"]=="core" for op in ops))
        self.assertEqual(3,len(llm.calls)); writer.assert_awaited_once()

    async def test_default_count_cannot_be_custom_even_if_model_approves(self):
        ops,_,writer=await self.run_turn("我有三只猫",
            [candidate("custom","三只",subject="用户的猫",predicate="数量",category="relationship")])
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_pet_kind_remembers_species_not_count(self):
        ops,_,_=await self.run_turn("我有两只猫",[candidate("pet_kind","猫",topic="猫",category="profile")])
        self.assertEqual("猫",ops[0]["fact"]["value"])
        self.assertEqual("relationship",ops[0]["category"])

    async def test_count_disguised_as_pet_kind_is_rejected(self):
        ops,_,writer=await self.run_turn("我有两只猫",[candidate("pet_kind","两只猫",topic="两只猫")])
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_count_disguised_as_relationship_is_rejected(self):
        ops,_,writer=await self.run_turn("请记住我有两只猫，我有三个杯子",[candidate("relationship","三个",topic="杯子")])
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_unknown_field_is_rejected(self):
        ops,_,_=await self.run_turn("我在市场买的",[candidate("purchase_source","市场")])
        self.assertEqual([],ops)

    async def test_purchase_and_travel_cannot_be_disguised_as_likes(self):
        for text,topic in (("我在市场买了一个杯子","杯子"),("我计划去日本旅游","旅游"),
                           ("我喜欢摄影，今天买了一个杯子","杯子")):
            ops,_,writer=await self.run_turn(text,[candidate("like",topic,topic=topic)])
            self.assertEqual([],ops,text); writer.assert_not_awaited()

    async def test_interests_have_independent_identity(self):
        ops,_,_=await self.run_turn("我喜欢摄影和游泳",[candidate("interest","摄影",topic="摄影"),candidate("interest","游泳",topic="游泳")])
        self.assertEqual(2,len(ops)); self.assertNotEqual(ops[0]["fact"]["predicateId"],ops[1]["fact"]["predicateId"])

    async def test_fabricated_topic_is_rejected(self):
        ops,_,_=await self.run_turn("我喜欢摄影",[candidate("interest","摄影",topic="游泳")])
        self.assertEqual([],ops)

    async def test_core_history_cannot_overwrite_current(self):
        item=candidate("student_status","大学生"); item["temporalScope"]="historical"
        ops,_,_=await self.run_turn("我以前是大学生",[item])
        self.assertEqual([],ops)

    async def test_old_age_can_be_updated_without_duplicate(self):
        old=memory(fact(value="20岁",evidence="我20岁"))
        ops,_,_=await self.run_turn("我21岁",[candidate("age","21岁")],[old])
        self.assertEqual("update",ops[0]["action"]); self.assertEqual(11,ops[0]["id"])

    async def test_explicit_custom_retains_authorization(self):
        ops,_,_=await self.run_turn("请记住我有两只猫",
            [candidate("custom","两只",subject="用户的猫",predicate="数量",category="relationship")])
        self.assertEqual("explicit",ops[0]["fact"]["memoryMode"])
        self.assertEqual("custom",ops[0]["fact"]["memoryField"])

    async def test_explicit_quantity_can_later_be_maintained(self):
        first,_,_=await self.run_turn("请记住我有两只猫",
            [candidate("custom","两只",subject="用户的猫",predicate="数量",category="relationship")])
        item=candidate("custom","一只",subject="用户的猫",predicate="数量",category="relationship",targetId=11,delta=1)
        item["intent"]="relative"
        ops,_,_=await self.run_turn("我又养了一只猫",[item],[record(first[0])])
        self.assertEqual("update",ops[0]["action"]); self.assertEqual("3只",ops[0]["fact"]["value"])
        self.assertEqual("explicit",ops[0]["fact"]["memoryMode"])

    async def test_legacy_count_does_not_grant_authorization(self):
        old=memory(fact("数量","两只",evidence="我有两只猫",subject="用户的猫"),category="relationship")
        item=candidate("custom","三只",subject="用户的猫",predicate="数量",category="relationship",targetId=11)
        ops,_,writer=await self.run_turn("现在我有三只猫",[item],[old])
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_species_repeat_does_not_create_new_record(self):
        first,_,_=await self.run_turn("我有两只猫",[candidate("pet_kind","猫",topic="猫")])
        ops,_,writer=await self.run_turn("我又养了一只猫",[candidate("pet_kind","猫",topic="猫")],[record(first[0])])
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_audit_rejection_still_blocks_core_fact(self):
        ops,_,writer=await self.run_turn("我21岁",[candidate("age","21岁")],approved=False)
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_context_cannot_supply_new_age(self):
        ops,_,_=await self.run_turn("你好",[candidate("age","21岁")],
            context=[NS(role="user",content="我21岁",created_at=WHEN)])
        self.assertEqual([],ops)

    async def test_cancelled_interest_deletes_same_topic(self):
        first,_,_=await self.run_turn("我喜欢摄影",[candidate("interest","摄影",topic="摄影")])
        item=candidate("interest","摄影",topic="摄影"); item["intent"]="cancelled"
        ops,_,_=await self.run_turn("我不再喜欢摄影",[item],[record(first[0])])
        self.assertEqual("delete",ops[0]["action"])

    async def test_refusal_blocks_entire_turn_before_model_or_database(self):
        for text in ("不要记住我今年21岁", "我21岁，这次不要保存", "请记住我有两只猫，但不要记住我喜欢摄影"):
            ops,llm,writer=await self.run_turn(text,[candidate("age","21岁")])
            self.assertEqual([],ops,text); self.assertEqual([],llm.calls); writer.assert_not_awaited()

    async def test_authorization_does_not_cover_other_clause_even_with_whole_quote(self):
        ops,_,writer=await self.run_turn("请记住我有两只猫，我有三个杯子",[
            candidate("custom","三个",subject="用户的杯子",predicate="数量",category="relationship")])
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_authorized_clause_is_saved_with_its_own_evidence(self):
        ops,_,_=await self.run_turn("请记住我有两只猫，我有三个杯子",[
            candidate("custom","两只",subject="用户的猫",predicate="数量",category="relationship")])
        self.assertEqual("请记住我有两只猫",ops[0]["fact"]["sourceEvidence"])

    async def test_interest_and_like_share_topic_and_opposite_updates_same_id(self):
        first,_,_=await self.run_turn("我对摄影感兴趣",[candidate("interest","摄影",topic="摄影")])
        repeated,_,writer=await self.run_turn("我喜欢摄影",[candidate("like","摄影",topic="摄影")],[record(first[0])])
        self.assertEqual([],repeated); writer.assert_not_awaited()
        changed,_,_=await self.run_turn("我现在不喜欢摄影",[candidate("dislike","摄影",topic="摄影")],[record(first[0])])
        self.assertEqual("update",changed[0]["action"]); self.assertEqual(11,changed[0]["id"])
        self.assertEqual("不喜欢摄影",changed[0]["fact"]["value"])

    async def test_exact_resolution_finds_core_record_not_in_extraction_window(self):
        old=memory(fact(value="20岁",evidence="我20岁"))
        provider=MemoryProvider({}); provider.init_memory("default",Llm({"facts":[candidate("age","21岁")]},review()))
        snapshots=[dict(schemaVersion=2,retrievalVersion=1,roleId="default",revision=5,memories=items) for items in ([],[old])]
        loader=AsyncMock(side_effect=snapshots); writer=AsyncMock(return_value=dict(updated=1))
        with patch(MODULE+".search_memory_facts",loader),patch(MODULE+".commit_memory_facts",writer):
            ops=await provider.save_memory([NS(role="user",content="我21岁",created_at=WHEN,uniq_id="test")])
        self.assertEqual("update",ops[0]["action"])
        self.assertEqual("extract",loader.await_args_list[0].kwargs["controlled_stage"])
        self.assertEqual("resolve",loader.await_args_list[1].kwargs["controlled_stage"])
        self.assertNotIn("memoryMode",loader.await_args_list[0].kwargs["terms"])

    async def test_changed_revision_or_incomplete_resolution_never_commits(self):
        for changed in ({"revision":6},{"truncated":True},{"retrievalVersion":0}):
            provider=MemoryProvider({}); provider.init_memory("default",Llm({"facts":[candidate("age","21岁")]}))
            snapshot=dict(schemaVersion=2,retrievalVersion=1,roleId="default",revision=5,memories=[])
            with patch(MODULE+".search_memory_facts",AsyncMock(side_effect=[snapshot,{**snapshot,**changed}])),patch(MODULE+".commit_memory_facts",AsyncMock()) as writer:
                result=await provider.save_memory([NS(role="user",content="我21岁",created_at=WHEN,uniq_id="test")])
            self.assertIsNone(result); writer.assert_not_awaited()

    async def test_feedback_only_claims_success_after_confirmed_commit(self):
        for response,expected in ((None,"未确认成功"),({"created":0},"没有新增"),({"created":1},"已确认保存")):
            provider=MemoryProvider({}); provider.init_memory("default",Llm({"facts":[candidate("age","21岁")]},review()))
            snapshot=dict(schemaVersion=2,retrievalVersion=1,roleId="default",revision=5,memories=[])
            with patch(MODULE+".search_memory_facts",AsyncMock(return_value=snapshot)),patch(MODULE+".commit_memory_facts",AsyncMock(return_value=response)):
                answer=await provider.prepare_memory_feedback([NS(role="user",content="请记住我21岁",created_at=WHEN,uniq_id="test")])
            self.assertIn(expected,answer)

    async def test_ordinary_chat_does_not_save_in_foreground(self):
        provider=MemoryProvider({}); provider.init_memory("default",Llm())
        with patch(MODULE+".search_memory_facts",AsyncMock()) as loader:
            answer=await provider.prepare_memory_feedback([NS(role="user",content="我21岁")])
        self.assertIsNone(answer); loader.assert_not_awaited()


class ExplicitRequestTests(unittest.TestCase):
    def test_positive_commands(self):
        for text in ("请记住我养两只猫","帮我记下我的旅行目标","记一下我在学摄影"):
            self.assertTrue(explicit_request(text),text)

    def test_queries_negations_and_quotes_are_not_authorization(self):
        for text in ("你记住了什么","不要记住我养两只猫","请记住了吗",'他说“记住我养两只猫”',"记住什么"):
            self.assertFalse(explicit_request(text),text)
