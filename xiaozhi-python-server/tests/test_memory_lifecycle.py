"""统一动态写入协议的生命周期回归；不会访问真实数据库。"""
import unittest
from test_memory import WHEN
import test_memory_policy as support
from test_memory_policy import record
from test_memory_categories import classified


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    run_turn=support.PolicyTests.run_turn

    async def test_change_correction_and_assertion_have_distinct_transitions(self):
        first,_,_=await self.run_turn("我是学生",[classified("职业","学生","profile")])
        for intent,transition in (("change","changed"),("corrected","corrected"),("assert","refines")):
            with self.subTest(intent=intent):
                item=classified("职业","工程师","profile"); item["intent"]=intent
                ops,_,_=await self.run_turn("我现在是工程师",[item],[record(first[0])])
                self.assertEqual(transition,ops[0]["transition"])
                self.assertEqual("update",ops[0]["action"])

    async def test_goal_completion_and_cancellation_preserve_terminal_record(self):
        first,_,_=await self.run_turn("我准备考过六级",[classified("六级目标","考过六级","goal")])
        for intent,category in (("completed","event"),("cancelled","goal")):
            with self.subTest(intent=intent):
                item=classified("六级目标","考过六级","goal"); item["intent"]=intent
                text="我已经实现考过六级的目标" if intent=="completed" else "我放弃考过六级这个目标"
                ops,_,_=await self.run_turn(text,[item],[record(first[0])])
                self.assertEqual("update",ops[0]["action"])
                self.assertEqual(intent,ops[0]["transition"])
                self.assertEqual(category,ops[0]["category"])
                self.assertEqual(category,ops[0]["fact"]["memoryField"])
                self.assertEqual("historical",ops[0]["fact"]["temporalScope"])

    async def test_invalidation_deletes_but_unresolved_terminal_goal_does_not(self):
        first,_,_=await self.run_turn("我准备考过六级",[classified("六级目标","考过六级","goal")])
        item=classified("六级目标","考过六级","goal"); item["intent"]="invalidated"
        ops,_,_=await self.run_turn("考过六级的目标记错了",[item],[record(first[0])])
        self.assertEqual("delete",ops[0]["action"])
        self.assertEqual("invalidated",ops[0]["transition"])
        item["intent"]="completed"
        ops,_,writer=await self.run_turn("我考过六级了",[item])
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_eight_dynamic_facts_are_accepted_and_nine_rejected(self):
        entries=[classified("偏好"+chr(0x7532+i),"喜欢摄影","preference") for i in range(8)]
        ops,_,_=await self.run_turn("我喜欢摄影",entries)
        self.assertEqual(8,len(ops))
        result,_,writer=await self.run_turn("我喜欢摄影",entries+[classified("额外偏好","喜欢摄影","preference")])
        self.assertIsNone(result); writer.assert_not_awaited()

    async def test_old_backend_protocol_cannot_silently_drop_history(self):
        from unittest.mock import AsyncMock, patch
        from types import SimpleNamespace as NS
        from test_memory import MODULE, Llm
        from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
        provider=MemoryProvider({"memory_policy":"legacy"}); provider.init_memory("default",Llm())
        snapshot=dict(schemaVersion=2,retrievalVersion=1,roleId="default",revision=1,memories=[])
        writer=AsyncMock()
        with patch(MODULE+".search_memory_facts",AsyncMock(return_value=snapshot)), patch(MODULE+".commit_memory_facts",writer):
            result=await provider.save_memory([NS(role="user",content="我是学生",created_at=WHEN,uniq_id="version-test")],"session")
        self.assertIsNone(result); writer.assert_not_awaited()
        self.assertEqual([],provider.llm.calls)


if __name__=="__main__": unittest.main()
