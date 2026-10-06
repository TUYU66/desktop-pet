"""集合与个体的受控写入回归；模拟模型和数据库，不证明真实模型的语义准确率。"""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from test_memory import MODULE, WHEN, review
from test_memory_management import ScriptedLlm, resolution
from test_memory_policy import record
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
from core.providers.memory.mem_local_short.management import RESOLVE_PROMPT
from core.providers.memory.mem_local_short.facts import numeric_quantity


def entry(subject, predicate, value, **extra):
    return dict(field="classified", subject=subject, predicate=predicate, value=value,
                category="relationship", temporalScope="current", intent="assert",
                sourceId=0) | extra


class AggregateTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, text, entries, memories=(), approved=True):
        # 显式使用生产 controlled 路径；仅替换模型返回与外部数据库。
        names=[resolution(e["subject"],e["predicate"],
                          "quantity" if e["predicate"]=="数量" else "pet_attribute") for e in entries]
        llm=ScriptedLlm([{"facts":entries}, *[review(subjectSupported=approved) for _ in entries]],
                        {RESOLVE_PROMPT:names})
        provider=MemoryProvider({}); provider.init_memory("default",llm)
        loader=AsyncMock(return_value=dict(schemaVersion=2,retrievalVersion=1,writeProtocolVersion=3,
            roleId="default",revision=5,memories=list(memories),truncated=False))
        writer=AsyncMock(return_value=dict(created=0,updated=0,deleted=0))
        with patch(MODULE+".search_memory_facts",loader), patch(MODULE+".commit_memory_facts",writer):
            result=await provider.save_memory([NS(role="user",content=text,created_at=WHEN,
                uniq_id="aggregate-test")],"synthetic-session")
        return result,writer

    async def aggregates(self):
        ops,_=await self.run_turn("我有三只猫两条狗",[
            entry("用户的猫","数量","三只"),entry("用户的狗","数量","两条")])
        self.assertEqual(2,len(ops))
        return ops

    async def test_two_collections_share_evidence_and_predicate_but_not_entity(self):
        ops=await self.aggregates()
        self.assertEqual({"用户的猫","用户的狗"},{o["fact"]["subject"] for o in ops})
        self.assertEqual(2,len({o["fact"]["entityId"] for o in ops}))
        self.assertEqual(1,len({o["fact"]["predicateId"] for o in ops}))
        self.assertTrue(all(o["fact"]["sourceEvidence"]=="我有三只猫两条狗" for o in ops))
        self.assertTrue(all(o["fact"]["memoryMode"]=="automatic" for o in ops))

    def test_fallback_normalizes_id_and_ignores_model_evidence(self):
        provider=MemoryProvider({})
        result=provider._referenced_source(entry("用户的猫","数量","三只",
            sourceId=2,evidence="改写的证据"),{"latestUser":"我有三只猫两条狗"})
        self.assertEqual(0,result["sourceId"])
        self.assertEqual("我有三只猫两条狗",result["evidence"])

    async def test_invented_fact_indices_fall_back_without_losing_either_collection(self):
        ops,_=await self.run_turn("我有三只猫两条狗",[
            entry("用户的猫","数量","三只",sourceId=1),
            entry("用户的狗","数量","两条",sourceId=2)])
        self.assertEqual(2,len(ops))
        self.assertEqual({"三只","两条"},{o["fact"]["value"] for o in ops})

    async def test_relative_change_updates_only_matching_collection(self):
        first=await self.aggregates()
        memories=[record(op,11+i) for i,op in enumerate(first)]
        ops,_=await self.run_turn("我又养了一只猫",[
            entry("用户的猫","数量","增加一只",intent="relative",delta=1)],memories)
        self.assertEqual(1,len(ops))
        self.assertEqual(("update",11),(ops[0]["action"],ops[0]["id"]))
        self.assertEqual(first[0]["fact"]["entityId"],ops[0]["fact"]["entityId"])
        self.assertEqual((4,"只"),numeric_quantity(ops[0]["fact"]["value"]))

    async def test_naming_one_member_adds_individual_without_changing_count(self):
        first=await self.aggregates()
        ops,_=await self.run_turn("其中一只猫叫小花",[
            entry("宠物：小花","名字","小花"),entry("宠物：小花","种类","猫")],
            [record(op,11+i) for i,op in enumerate(first)])
        self.assertEqual(2,len(ops))
        self.assertTrue(all(o["action"]=="add" for o in ops))
        self.assertEqual(1,len({o["fact"]["entityId"] for o in ops}))
        self.assertTrue({o["fact"]["entityId"] for o in ops}.isdisjoint(
            {o["fact"]["entityId"] for o in first}))

    async def test_hypothesis_and_wrong_owner_are_blocked_by_semantic_rejection(self):
        for text,raw in [
            ("如果以后养猫我想叫小花",entry("宠物：小花","名字","小花")),
            ("我朋友有两只猫",entry("用户的猫","数量","两只")),
        ]:
            with self.subTest(text=text):
                ops,writer=await self.run_turn(text,[raw],approved=False)
                self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_other_owner_and_non_pet_collections_have_separate_entities(self):
        ops,_=await self.run_turn("我有两个姐姐，我朋友有两只猫",[
            entry("用户的姐姐","数量","两个"),entry("朋友的猫","数量","两只")])
        self.assertEqual(2,len(ops))
        self.assertEqual(2,len({o["fact"]["entityId"] for o in ops}))


if __name__=="__main__": unittest.main()
