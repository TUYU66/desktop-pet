"""提取结构、时间修复和聚焦审查回归；模型与数据库使用桩。"""
import unittest
from unittest.mock import AsyncMock
from test_memory import WHEN
from test_memory_policy import record
import test_memory_policy as support
from test_memory_aggregates import entry
import test_memory_aggregates as aggregates
from core.providers.memory.mem_local_short.protocol import decode_candidate, repair_candidate_time, preferred_label
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider


class ExtractionContractTests(unittest.IsolatedAsyncioTestCase):
    run_turn=support.PolicyTests.run_turn

    async def test_chinese_display_fields_keep_correct_category(self):
        raw=[dict(方式="classified",类别=cat,对象="user",属性=attr,内容=value,意图="assert",时间范围="current",原话编号=0)
             for cat,attr,value in (("habit","惯用手","左手"),("preference","西红柿偏好","喜欢吃西红柿"))]
        ops,llm,_=await self.run_turn("我是左撇子，喜欢吃西红柿",raw)
        self.assertEqual({"habit","preference"},{o["category"] for o in ops})
        self.assertEqual({"惯用手","西红柿偏好"},{o["fact"]["predicate"] for o in ops})
        for _,payload in llm.calls[1:]:
            self.assertNotIn("originalFact",payload["sourceFact"])
            self.assertNotIn("recentEntities",payload)
            self.assertEqual([],payload["userContext"])

    def test_conflicting_bilingual_protocol_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"candidate_protocol_conflict"):
            decode_candidate({"类别":"habit","category":"profile"})

    async def test_unknown_future_date_is_repaired_once_without_changing_content(self):
        provider=MemoryProvider({})
        provider._ask_json=AsyncMock(return_value=dict(temporalScope="future",timePrecision="approximate",timeValue="未定日期"))
        text="我准备计划去美国洛杉矶"
        raw=dict(field="classified",subject="user",predicate="洛杉矶旅行计划",value="计划去美国洛杉矶",category="goal",
                 intent="assert",temporalScope="future",timePrecision="day",evidence=text)
        result,repaired=await repair_candidate_time(provider,raw,{"latestUser":text,"referenceTime":WHEN.isoformat()},WHEN.date())
        self.assertTrue(repaired)
        self.assertEqual("未定日期",result["timeValue"])
        for key in ("subject","predicate","value","category","intent","evidence"):
            self.assertEqual(raw[key],result[key])
        provider._ask_json.assert_awaited_once()

    async def test_valid_current_plan_needs_no_extra_request(self):
        provider=MemoryProvider({}); provider._ask_json=AsyncMock()
        raw=dict(subject="user",predicate="旅行计划",value="计划去洛杉矶",category="goal",intent="assert",temporalScope="current",evidence="我计划去洛杉矶")
        _,repaired=await repair_candidate_time(provider,raw,{"latestUser":raw["evidence"]},WHEN.date())
        self.assertFalse(repaired); provider._ask_json.assert_not_awaited()

    def test_old_english_name_does_not_replace_new_chinese_label(self):
        self.assertEqual("西红柿偏好",preferred_label("西红柿偏好","preference"))


class CorrectionContractTests(unittest.IsolatedAsyncioTestCase):
    run_turn=aggregates.AggregateTests.run_turn
    aggregates=aggregates.AggregateTests.aggregates

    async def test_two_collection_corrections_update_separate_records(self):
        previous,_=await self.run_turn("我养了三只猫两只狗",[
            entry("用户的猫","数量","三只"),entry("用户的狗","数量","两只")])
        ops,_=await self.run_turn("我说错了，我是养了两只猫三只狗",[
            entry("用户的猫","数量","两只",intent="corrected"),
            entry("用户的狗","数量","三只",intent="corrected")],
            [record(previous[0],11),record(previous[1],12)])
        self.assertEqual(2,len(ops))
        self.assertEqual({11,12},{op["id"] for op in ops})
        self.assertTrue(all(op["transition"]=="corrected" for op in ops))


if __name__=="__main__": unittest.main()
