"""交接问题的故障注入回归；所有数据库接口均使用合成快照。"""
import unittest
import test_memory_management as fixtures
from test_memory import fact, memory, extract, align, review
from core.providers.memory.mem_local_short.management import SOURCE_REPAIR_PROMPT, COVERAGE_PROMPT, RESOLVE_PROMPT


class RepairRegressions(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, *args, **kwargs):
        return await fixtures.ManagementTests.run_turn(self, *args, **kwargs)

    async def test_rejected_whole_quote_allows_other_clause(self):
        text = "我21岁，我喜欢蓝色"
        bad = extract(fact(evidence=text)); bad["facts"][0]["delta"] = 1
        preference = fact("喜欢的颜色", "蓝色", evidence=text)
        result, _, _, _ = await self.run_turn(text, [bad, align(preference), review()],
            {COVERAGE_PROMPT: [{"missingFacts": extract(preference)["facts"]}]})
        self.assertEqual(["蓝色"], [op["fact"]["value"] for op in result])

    async def test_shortened_quote_cannot_bypass_rejection(self):
        text = "我现在21岁"
        bad = extract(fact(evidence=text)); bad["facts"][0]["delta"] = 1
        other = fact("身份", "年轻人", evidence="21岁")
        result, _, _, writer = await self.run_turn(text, [bad],
            {COVERAGE_PROMPT: [{"missingFacts": extract(other)["facts"]}]})
        self.assertEqual([], result); writer.assert_not_awaited()

    async def test_source_repair_cannot_swap_to_unrelated_fact(self):
        text = "我21岁，我喜欢蓝色"
        bad = extract(fact(evidence="我21岁")); bad["facts"][0]["delta"] = 1
        other = extract(fact("喜欢的颜色", "蓝色", evidence="我喜欢蓝色"))["facts"][0]
        result, _, _, writer = await self.run_turn(text, [bad],
            {SOURCE_REPAIR_PROMPT: [{"correctedSource": other}]})
        self.assertEqual([], result); writer.assert_not_awaited()

    async def test_string_repair_is_never_coerced_to_fact(self):
        bad = extract(fact()); bad["facts"][0]["delta"] = 1
        result, _, _, writer = await self.run_turn("我21岁", [bad],
            {SOURCE_REPAIR_PROMPT: [{"correctedSource": "用户年龄21岁"}]})
        self.assertEqual([], result); writer.assert_not_awaited()

    async def test_duplicate_failed_extraction_has_only_one_repair(self):
        raw = extract(fact())["facts"][0] | {"delta": 1}
        result, llm, _, _ = await self.run_turn("我21岁", [{"facts": [raw, raw]}])
        self.assertEqual([], result)
        self.assertEqual(1, sum(s == SOURCE_REPAIR_PROMPT for s, _ in llm.all_calls))

    async def test_rephrased_failed_assertion_does_not_gain_second_repair(self):
        raw = extract(fact())["facts"][0] | {"delta": 1}
        result, llm, _, _ = await self.run_turn("我21岁",
            [{"facts": [raw, raw | {"category": "note"}]}])
        self.assertEqual([], result)
        self.assertEqual(1, sum(s == SOURCE_REPAIR_PROMPT for s, _ in llm.all_calls))

    async def test_blocked_broad_candidate_does_not_poison_other_clause(self):
        text = "我21岁，我喜欢蓝色"
        bad = extract(fact(evidence=text)); bad["facts"][0]["delta"] = 1
        broad = fact("身份", "年轻人", evidence=text)
        preference = fact("喜欢的颜色", "蓝色", evidence="我喜欢蓝色")
        result, _, _, _ = await self.run_turn(text, [bad, align(preference), review()],
            {COVERAGE_PROMPT: [{"missingFacts": extract(broad, preference)["facts"]}]})
        self.assertEqual(["蓝色"], [op["fact"]["value"] for op in result])

    async def test_nonnumeric_update_cannot_invent_another_value(self):
        text = "我现在住在上海"
        source = fact("住所", "上海", evidence=text)
        old = memory(fact("住所", "北京", evidence="我住在北京"))
        result, llm, _, writer = await self.run_turn(text,
            [extract(source), align(source | {"value": "巴黎"}, "update", 11, "changed"), review()],
            memories=[old])
        self.assertEqual([], result)
        self.assertEqual([], writer.call_args.args[-1])
        self.assertEqual(2, len(llm.calls))

    async def test_embedded_hallucinated_number_is_rejected(self):
        text = "去年在市场买的"
        result, _, _, writer = await self.run_turn(text,
            [extract(fact(value="今年28岁", evidence=text))])
        self.assertEqual([], result); writer.assert_not_awaited()

    async def test_coverage_gets_one_source_repair_for_missing_time(self):
        text = "去年我住在上海"
        raw = extract(fact("住所", "上海", "historical", text))["facts"][0]
        broken = {k:v for k,v in raw.items() if k not in ("timePrecision", "timeValue")}
        result, llm, _, _ = await self.run_turn(text,
            [extract(), align(raw, category="event"), review()],
            {COVERAGE_PROMPT: [{"missingFacts": [broken]}],
             SOURCE_REPAIR_PROMPT: [{"correctedSource": raw}]})
        self.assertEqual("2025", result[0]["fact"]["timeValue"])
        self.assertEqual(1, sum(s == SOURCE_REPAIR_PROMPT for s, _ in llm.all_calls))

    async def test_new_predicate_chinese_code_uses_local_identifier(self):
        text = "我大三了"; source = fact("年级", "大三", evidence=text)
        result, _, _, _ = await self.run_turn(text,
            [extract(source), align(source), review()],
            {RESOLVE_PROMPT: [fixtures.resolution("user", "年级", "年级")]})
        self.assertRegex(result[0]["fact"]["canonicalPredicate"], r"^attr_[a-f0-9]{16}$")

    async def test_relative_assertion_cannot_be_downgraded_by_coverage(self):
        text="我又多了一辆车"
        raw=extract(fact("数量","增加一辆",evidence=text,subject="用户的车"))
        raw["facts"][0].update(intent="relative",delta=1)
        broad=fact("持有物","车",evidence=text)
        result,_,_,writer=await self.run_turn(text,[raw,{"action":"none"}],
            {COVERAGE_PROMPT:[{"missingFacts":extract(broad)["facts"]}]})
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])

    async def test_conflicting_add_cannot_poison_entire_commit(self):
        text="我21岁，也是大学生"
        age=fact(evidence="我21岁"); student=fact("职业","大学生",evidence="也是大学生")
        age_resolution=fixtures.resolution("user","年龄","age")
        def wrong_resolution(data):
            predicate=data["predicateCatalog"][0]
            return fixtures.resolution("user",predicate["label"],predicate["code"]) | {"predicateId":predicate["id"]}
        result,_,_,writer=await self.run_turn(text,
            [extract(age,student),align(age),review(),align(student)],
            {RESOLVE_PROMPT:[age_resolution,wrong_resolution]})
        self.assertEqual(["21岁"],[op["fact"]["value"] for op in result])
        self.assertEqual(1,len(writer.call_args.args[-1]))


if __name__ == "__main__":
    unittest.main()
