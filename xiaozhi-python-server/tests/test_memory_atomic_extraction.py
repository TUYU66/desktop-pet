"""复合数量、漏项复核和时间修复返回格式；无真实模型或数据库调用。"""
import unittest
from unittest.mock import AsyncMock
from test_memory import WHEN
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
from core.providers.memory.mem_local_short.categories import AUTO_CATEGORIES
from core.providers.memory.mem_local_short.facts import numeric_quantity
from core.providers.memory.mem_local_short.protocol import review_extraction, repair_candidate_time


def candidate(subject,predicate,value,category="relationship",intent="assert"):
    return dict(field="classified",subject=subject,predicate=predicate,value=value,category=category,
                intent=intent,temporalScope="current",sourceId=0)


class AtomicExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_review_does_not_erase_original_candidates(self):
        provider=MemoryProvider({})
        provider._ask_json=AsyncMock(return_value={"facts":[]})
        values=[candidate("user","宠物","三只猫两只狗")]
        result=await review_extraction(provider,values,{"latestUser":"我养了三只猫两只狗"},AUTO_CATEGORIES)
        self.assertEqual(values,result)

    async def test_review_missing_automatic_marker_keeps_atomic_facts(self):
        provider=MemoryProvider({})
        revised=[candidate("用户的猫","数量","三只"),candidate("用户的狗","数量","两只")]
        provider._ask_json=AsyncMock(return_value={"facts":[{k:v for k,v in c.items() if k!="field"} for c in revised]})
        result=await review_extraction(provider,[candidate("user","宠物","三只猫两只狗")],
                                      {"latestUser":"我养了三只猫两只狗"},AUTO_CATEGORIES)
        self.assertEqual(revised,result)

    async def test_review_cannot_grant_target_authority(self):
        provider=MemoryProvider({})
        provider._ask_json=AsyncMock(return_value={"facts":[{**candidate("用户的猫","数量","三只"),"targetId":"forged"}]})
        values=[candidate("user","宠物","三只猫两只狗")]
        self.assertEqual(values,await review_extraction(provider,values,{"latestUser":"我养了三只猫两只狗"},AUTO_CATEGORIES))

    def test_compound_numbers_are_never_a_single_number_with_a_long_unit(self):
        for text in ("三只猫两只狗","两只猫三只狗","3台打印机2台电脑"):
            self.assertIsNone(numeric_quantity(text))
        self.assertEqual((3,"只"),numeric_quantity("三只"))

    async def test_compound_candidate_is_replaced_by_two_atomic_corrections(self):
        provider=MemoryProvider({})
        revised=[candidate("用户的猫","数量","两只",intent="corrected"),candidate("用户的狗","数量","三只",intent="corrected")]
        provider._ask_json=AsyncMock(return_value={"facts":revised})
        base={"latestUser":"我说错了，我养了两只猫三只狗"}
        result=await review_extraction(provider,[candidate("user","宠物","两只猫三只狗",intent="corrected")],base,AUTO_CATEGORIES)
        self.assertEqual(revised,result); provider._ask_json.assert_awaited_once()

    async def test_missing_clause_can_be_recovered_before_audit(self):
        provider=MemoryProvider({})
        habit=candidate("user","惯用手","左手","habit")
        preference=candidate("user","西红柿偏好","喜欢吃西红柿","preference")
        provider._ask_json=AsyncMock(return_value={"facts":[habit,preference]})
        result=await review_extraction(provider,[preference],{"latestUser":"我是左撇子，喜欢吃西红柿"},AUTO_CATEGORIES)
        self.assertEqual([habit,preference],result)

    def test_unchanged_compound_candidate_cannot_pass_source_validation(self):
        provider=MemoryProvider({})
        text="我养了三只猫两只狗"
        with self.assertRaisesRegex(ValueError,"compound_relationship_requires_atomic_facts"):
            provider._source({**candidate("user","宠物","三只猫两只狗"),"evidence":text},{"latestUser":text},WHEN.date())

    async def test_partial_chinese_time_patch_keeps_original_scope(self):
        provider=MemoryProvider({})
        provider._ask_json=AsyncMock(return_value={"时间精度":"approximate","时间值":"未定日期"})
        text="我计划去洛杉矶"
        raw={**candidate("user","旅行计划","计划去洛杉矶","goal"),"temporalScope":"future","evidence":text}
        fixed,repaired=await repair_candidate_time(provider,raw,{"latestUser":text},WHEN.date())
        self.assertTrue(repaired); self.assertEqual("future",fixed["temporalScope"])
        self.assertEqual("未定日期",fixed["timeValue"])

    async def test_normal_single_fact_does_not_add_review_request(self):
        provider=MemoryProvider({}); provider._ask_json=AsyncMock()
        values=[candidate("用户的猫","数量","三只")]
        self.assertEqual(values,await review_extraction(provider,values,{"latestUser":"我养了三只猫"},AUTO_CATEGORIES))
        provider._ask_json.assert_not_awaited()


if __name__=="__main__": unittest.main()
