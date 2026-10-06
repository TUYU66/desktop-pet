"""正常生产路径测试：不能依靠模型提案或计算器来决定已确定事实槽。"""
import unittest
import test_memory_management as fixtures
from test_memory import fact, memory, extract, review
from core.providers.memory.mem_local_short.management import RESOLVE_PROMPT, REPAIR_PROMPT, SOURCE_REPAIR_PROMPT, COVERAGE_PROMPT, Registry
from core.providers.memory.mem_local_short.mem_local_short import ALIGN_PROMPT, AUDIT_PROMPT


class PlanningTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self,*args,**kwargs):
        return await fixtures.ManagementTests.run_turn(self,*args,**kwargs,production=True)

    async def test_new_user_age_skips_naming_and_alignment_but_not_semantic_audit(self):
        cats=memory(fact("数量","两只",evidence="我有两只猫",subject="用户的猫"))
        result,llm,_,writer=await self.run_turn("我21岁",[extract(fact()),review()],memories=[cats])
        self.assertEqual("add",result[0]["action"])
        self.assertEqual("21岁",result[0]["fact"]["value"])
        phases=[system for system,_ in llm.all_calls]
        self.assertNotIn(RESOLVE_PROMPT,phases); self.assertNotIn(ALIGN_PROMPT,phases)
        self.assertIn(AUDIT_PROMPT,llm.calls[-1][0]); self.assertEqual(1,len(writer.call_args.args[-1]))

    async def test_multi_assertions_and_coverage_are_preserved_in_production_path(self):
        text="我是大学生，今年21岁"; student=fact("学籍","大学生",evidence="我是大学生")
        age=fact(evidence="今年21岁")
        result,llm,_,_=await self.run_turn(text,[extract(student),review(),review()],
            {COVERAGE_PROMPT:[{"missingFacts":extract(age)["facts"]}]})
        self.assertEqual({"学籍","年龄"},{op["fact"]["predicate"] for op in result})
        self.assertFalse(any(system==ALIGN_PROMPT for system,_ in llm.all_calls))

    async def test_canonical_numeric_delta_is_calculated_locally(self):
        text="我又多了一辆自行车"; subject="用户的自行车"
        old=memory(fact("数量","两辆",evidence="我有两辆自行车",subject=subject))
        source=fact("数量","增加一辆",evidence=text,subject=subject)
        payload=extract(source); payload["facts"][0].update(intent="relative",delta=1)
        result,llm,_,_=await self.run_turn(text,[payload,review()],memories=[old])
        self.assertEqual("3辆",result[0]["fact"]["value"]); self.assertEqual(11,result[0]["id"])
        self.assertFalse(any(system in (ALIGN_PROMPT,REPAIR_PROMPT) for system,_ in llm.all_calls))

    async def test_absolute_numeric_update_is_still_checked_against_source(self):
        old=memory(fact(value="20岁",evidence="我20岁"))
        result,llm,_,_=await self.run_turn("我21岁",[extract(fact()),review()],memories=[old])
        self.assertEqual("update",result[0]["action"]); self.assertEqual("21岁",result[0]["fact"]["value"])
        self.assertNotIn(ALIGN_PROMPT,[s for s,_ in llm.all_calls])

    async def test_repeating_same_value_needs_no_write_or_model_alignment(self):
        result,llm,_,writer=await self.run_turn("我21岁",[extract(fact())],memories=[memory(fact())])
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])
        self.assertEqual(1,len(llm.calls)); self.assertNotIn(ALIGN_PROMPT,[s for s,_ in llm.all_calls])

    async def test_past_new_slot_does_not_modify_current_slot(self):
        text="去年我住在上海"; source=fact("住所","上海","historical",text)
        old=memory(fact("住所","北京",evidence="我现在住在北京"))
        result,llm,_,_=await self.run_turn(text,[extract(source),review()],memories=[old])
        self.assertEqual("add",result[0]["action"]); self.assertEqual("2025",result[0]["fact"]["timeValue"])
        self.assertNotIn(ALIGN_PROMPT,[s for s,_ in llm.all_calls])

    async def test_fast_path_does_not_bypass_negative_semantic_audit(self):
        result,_,_,writer=await self.run_turn("我21岁",[extract(fact()),review(longTerm=False)])
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])

    async def test_saved_predicate_alias_can_match_without_naming_model(self):
        old=memory(fact())
        import json
        f=json.loads(old["factJson"]); catalog=Registry([old]); pid=next(iter(catalog.predicates))
        f.update(entityId="user",predicateId=pid,canonicalPredicate="age",predicateDefinition="陈述年龄",
                 entityAliases=["user"],predicateAliases=["年龄","今年多大"])
        old["factJson"]=json.dumps(f,ensure_ascii=False)
        source=fact("今年多大","22岁",evidence="我22岁")
        result,llm,_,_=await self.run_turn("我22岁",[extract(source),review()],memories=[old])
        self.assertEqual(pid,result[0]["fact"]["predicateId"])
        self.assertNotIn(RESOLVE_PROMPT,[s for s,_ in llm.all_calls])

    async def test_naming_id_label_mismatch_can_only_retry_once(self):
        text="我的车是红色"; source=fact("颜色","红色",evidence=text,subject="用户的车")
        old=memory(fact("颜色","蓝色",evidence="我的车是蓝色",subject="用户的车"))
        registry=Registry([old]); pid=next(iter(registry.predicates))
        bad=fixtures.resolution("用户的车","年龄","age"); bad["predicateId"]=pid
        from types import SimpleNamespace as NS
        from test_memory import WHEN
        result,llm,_,writer=await self.run_turn(text,[extract(source)],{RESOLVE_PROMPT:[bad,bad]},memories=[old],
            context=[NS(role="user",content="我的车",created_at=WHEN)])
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])
        self.assertEqual(2,sum(s==RESOLVE_PROMPT for s,_ in llm.all_calls))

    async def test_numeric_object_suffix_cannot_create_unrelated_absolute_slot(self):
        old=memory(fact("数量","2台",evidence="我有2台打印机",subject="用户的打印机"))
        source=fact("设备","1台打印机",evidence="我新增1台打印机")
        result,llm,_,writer=await self.run_turn("我新增1台打印机",[extract(source)],memories=[old])
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])
        self.assertEqual(1,sum(s==REPAIR_PROMPT for s,_ in llm.all_calls))

    async def test_delta_and_assert_are_not_silently_interchangeable(self):
        source=extract(fact()); source["facts"][0]["delta"]=1
        result,llm,_,writer=await self.run_turn("我21岁",[source])
        self.assertEqual([],result); writer.assert_not_awaited()
        self.assertEqual(1,sum(s==SOURCE_REPAIR_PROMPT for s,_ in llm.all_calls))

    async def test_failed_source_repair_cannot_be_bypassed_by_coverage(self):
        source=extract(fact()); source["facts"][0]["delta"]=1
        wrong=fact("身份","年轻人",evidence="我21岁")
        result,llm,_,writer=await self.run_turn("我21岁",[source],
            {COVERAGE_PROMPT:[{"missingFacts":extract(wrong)["facts"]}]})
        self.assertEqual([],result); writer.assert_not_awaited()

    async def test_source_intent_repair_does_not_require_old_total(self):
        text="我又多了一台打印机"; subject="用户的打印机"
        old=memory(fact("数量","2台",evidence="我有2台打印机",subject=subject))
        f=fact("数量","增加1台",evidence=text,subject=subject)
        bad=extract(f); bad["facts"][0].update(delta=1)
        corrected=extract(f)["facts"][0]|{"intent":"relative","delta":1}
        result,llm,_,_=await self.run_turn(text,[bad,review()],
            {SOURCE_REPAIR_PROMPT:[{"correctedSource":corrected}]},memories=[old])
        self.assertEqual("3台",result[0]["fact"]["value"])
        self.assertNotIn(REPAIR_PROMPT,[s for s,_ in llm.all_calls])

    async def test_hallucinated_number_cannot_pass_even_with_true_audit(self):
        text="去年在市场买的"; invented=fact(value="28岁",evidence=text)
        result,llm,_,writer=await self.run_turn(text,[extract(invented)])
        self.assertEqual([],result); writer.assert_not_awaited()
        self.assertEqual(1,len(llm.calls))


if __name__=="__main__": unittest.main()
