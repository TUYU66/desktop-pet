"""原文引用ID与上下文目录连接；证据文本仍须本地验证。"""
import unittest
from types import SimpleNamespace as NS
import test_memory_management as fixtures
from test_memory import fact, memory, extract, align, review, WHEN
from core.providers.memory.mem_local_short.mem_local_short import ALIGN_PROMPT
from core.providers.memory.mem_local_short.management import RECENT_PROMPT, RESOLVE_PROMPT, SOURCE_REPAIR_PROMPT, ENTITY_BIND_PROMPT


class SourceReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self,*args,**kwargs):
        return await fixtures.ManagementTests.run_turn(self,*args,**kwargs,production=True)

    async def test_reference_uses_original_text_not_model_rewritten_quote(self):
        text="我现在21岁"; raw=extract(fact(evidence="用户21岁"))
        raw["facts"][0]["sourceId"]=0
        result,_,_,_=await self.run_turn(text,[raw,review()])
        self.assertEqual(text,result[0]["fact"]["sourceEvidence"])

    async def test_single_segment_invalid_reference_falls_back_to_original(self):
        for index in (99,True,"0",-1):
            with self.subTest(index=index):
                raw=extract(fact()); raw["facts"][0]["sourceId"]=index
                result,_,_,_=await self.run_turn("我21岁",[raw,review()])
                self.assertEqual("我21岁",result[0]["fact"]["sourceEvidence"])

    async def test_multi_segment_invalid_reference_is_rejected(self):
        for index in (99,True,"0",-1):
            with self.subTest(index=index):
                raw=extract(fact()); raw["facts"][0]["sourceId"]=index
                result,_,_,writer=await self.run_turn("我21岁，我喜欢摄影",[raw])
                self.assertEqual([],result); writer.assert_not_awaited()

    async def test_fallback_still_rejects_unmentioned_number(self):
        raw=extract(fact(value="28岁")); raw["facts"][0]["sourceId"]=99
        result,_,_,writer=await self.run_turn("我21岁",[raw])
        self.assertEqual([],result); writer.assert_not_awaited()

    async def test_fallback_still_requires_semantic_audit(self):
        raw=extract(fact()); raw["facts"][0]["sourceId"]=99
        result,_,_,writer=await self.run_turn("我21岁",[raw,review(attributeSupported=False)])
        self.assertEqual([],result)
        self.assertEqual([],writer.call_args.args[-1])

    async def test_reference_does_not_approve_unmentioned_number(self):
        raw=extract(fact(value="28岁")); raw["facts"][0]["sourceId"]=0
        result,_,_,writer=await self.run_turn("我21岁",[raw])
        self.assertEqual([],result); writer.assert_not_awaited()

    async def test_reference_still_requires_semantic_audit(self):
        raw=extract(fact()); raw["facts"][0]["sourceId"]=0
        result,_,_,writer=await self.run_turn("我21岁",[raw,review(attributeSupported=False)])
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])

    async def test_reference_is_preserved_through_historical_source_repair(self):
        text="去年我有两台打印机"
        raw=extract(fact("数量","增加两台","historical",text,subject="用户的打印机"))["facts"][0]
        raw.pop("timePrecision"); raw.pop("timeValue")
        raw.update(sourceId=0,intent="relative",delta=2)
        corrected={**raw,"intent":"assert","value":"两台","timePrecision":"year","timeValue":"2025"}
        corrected.pop("delta")
        current=memory(fact("数量","3台",evidence="我有3台打印机",subject="用户的打印机"))
        result,_,_,_=await self.run_turn(text,[{"facts":[raw]},review()],
            {SOURCE_REPAIR_PROMPT:[{"correctedSource":corrected}]},memories=[current])
        self.assertEqual("add",result[0]["action"])
        self.assertEqual("historical",result[0]["fact"]["temporalScope"])
        self.assertEqual("两台",result[0]["fact"]["value"])

    async def test_context_modifier_does_not_prevent_exact_saved_entity_binding(self):
        text="对啊我去年大二的时候在市场买的"; quote="我养的两只猫现在两岁"
        old=memory(fact("购买来源","大二期间购买","historical","去年大二买了猫",subject="用户的猫"),category="event")
        source=extract(fact("购买来源","市场","historical",text,subject="两只猫"))
        source["facts"][0]["sourceId"]=0
        r=fixtures.resolution("用户的猫","购买来源","purchase_source",contextIndex=0,contextEvidence=quote)
        r["relationToPreviousFact"]="context"
        result,_,_,_=await self.run_turn(text,[source,review()],
            {RECENT_PROMPT:[{"entities":[{"name":"两只猫","aliases":["猫"],"messageIndex":0,"evidence":quote}]}],
             RESOLVE_PROMPT:[r],ALIGN_PROMPT:[align(source["facts"][0],"update",11,"refines",category="event")]},
            memories=[old],context=[NS(role="user",content=quote,created_at=WHEN)])
        self.assertEqual("update",result[0]["action"])
        self.assertEqual(11,result[0]["id"])
        self.assertEqual("用户的猫",result[0]["fact"]["subject"])
        self.assertEqual("市场",result[0]["fact"]["value"])

    async def test_context_link_supplies_standard_object_and_verified_provenance(self):
        text="去年在市场买的"; quote="我有两台打印机"
        old=memory(fact("购买来源","商店","historical","去年在商店买的",subject="用户的打印机"))
        from core.providers.memory.mem_local_short.management import Registry
        eid=next(e for e in Registry([old]).entities if e!="user")
        source=extract(fact("购买来源","市场","historical",text,subject="两台打印机"))
        source["facts"][0]["sourceId"]=0
        r=fixtures.resolution("两台打印机","购买来源","purchase_source")
        result,llm,_,_=await self.run_turn(text,[source,review()],
            {RECENT_PROMPT:[{"entities":[{"name":"两台打印机","aliases":["打印机"],"messageIndex":0,"evidence":quote}]}],
             ENTITY_BIND_PROMPT:[{"bindings":[{"recentIndex":0,"entityId":eid}]}],RESOLVE_PROMPT:[r]},
            memories=[old],context=[NS(role="user",content=quote,created_at=WHEN)])
        self.assertEqual("update",result[0]["action"])
        self.assertEqual("用户的打印机",result[0]["fact"]["subject"])
        self.assertEqual(quote,result[0]["fact"]["contextEvidence"])
        self.assertNotIn(ALIGN_PROMPT,[system for system,_ in llm.all_calls])

    async def test_context_link_unknown_directory_id_cannot_be_used(self):
        text="去年在市场买的"; quote="我有两台打印机"
        old=memory(fact("购买来源","商店","historical","去年在商店买的",subject="用户的打印机"))
        source=extract(fact("购买来源","市场","historical",text,subject="两台打印机"))
        result,_,_,writer=await self.run_turn(text,[source],
            {RECENT_PROMPT:[{"entities":[{"name":"两台打印机","aliases":[],"messageIndex":0,"evidence":quote}]}],
             ENTITY_BIND_PROMPT:[{"bindings":[{"recentIndex":0,"entityId":"e_unknown"}]}]},
            memories=[old],context=[NS(role="user",content=quote,created_at=WHEN)])
        self.assertIsNone(result); writer.assert_not_awaited()


if __name__=="__main__": unittest.main()
