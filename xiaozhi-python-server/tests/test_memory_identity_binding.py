"""属性归一与相对数量目标绑定；采用生产流程和合成数据库。"""
import unittest
import test_memory_management as fixtures
from test_memory import fact, memory, extract, review
from core.providers.memory.mem_local_short.management import RESOLVE_PROMPT, QUANTITY_BIND_PROMPT, REPAIR_PROMPT


class IdentityBindingTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, *args, **kwargs):
        return await fixtures.ManagementTests.run_turn(self, *args, **kwargs, production=True)

    async def test_new_attribute_cannot_be_renamed_to_age_without_catalog_id(self):
        text="是啊我现在是大学生21岁"
        age=fact(evidence=text); student=fact("职业","大学生",evidence=text)
        bad=fixtures.resolution("user","年龄","attr_9ee831fcb82d7223")
        result,llm,_,_=await self.run_turn(text,[extract(age,student),review(),review()],
            {RESOLVE_PROMPT:[bad]})
        self.assertEqual({"年龄":"21岁","职业":"大学生"},
                         {op["fact"]["predicate"]:op["fact"]["value"] for op in result})
        self.assertEqual(2,len({op["fact"]["predicateId"] for op in result}))

    async def test_code_collision_does_not_merge_different_attributes(self):
        old=memory(fact())
        from core.providers.memory.mem_local_short.management import Registry
        registry=Registry([old]); age_code=next(iter(registry.predicates.values()))["code"]
        text="我是大学生"; student=fact("职业","大学生",evidence=text)
        result,_,_,_=await self.run_turn(text,[extract(student),review()],
            {RESOLVE_PROMPT:[fixtures.resolution("user","职业",age_code)]},memories=[old])
        self.assertEqual("职业",result[0]["fact"]["predicate"])
        self.assertNotEqual(next(iter(registry.predicates)),result[0]["fact"]["predicateId"])

    async def test_coarse_relative_assertion_binds_to_quantity_and_calculates_locally(self):
        text="我加养了一只猫"
        old=memory(fact("数量","两只",evidence="我有两只猫",subject="用户的猫"))
        raw=extract(fact("宠物","增加一只猫",evidence=text))
        raw["facts"][0].update(intent="relative",delta=1)
        result,llm,_,_=await self.run_turn(text,[raw,review()],
            {QUANTITY_BIND_PROMPT:[{"targetId":11,"sameQuantity":True,"unit":"只"}]},memories=[old])
        self.assertEqual("3只",result[0]["fact"]["value"])
        self.assertEqual("用户的猫",result[0]["fact"]["subject"])
        self.assertEqual(11,result[0]["id"])
        self.assertNotIn(REPAIR_PROMPT,[s for s,_ in llm.all_calls])

    async def test_same_binding_applies_to_non_pet_objects(self):
        text="我又买了一台打印机"
        old=memory(fact("数量","2台",evidence="我有2台打印机",subject="用户的打印机"))
        raw=extract(fact("设备","增加一台打印机",evidence=text))
        raw["facts"][0].update(intent="relative",delta=1)
        result,_,_,_=await self.run_turn(text,[raw,review()],
            {QUANTITY_BIND_PROMPT:[{"targetId":11,"sameQuantity":True,"unit":"台"}]},memories=[old])
        self.assertEqual("3台",result[0]["fact"]["value"])

    async def test_ambiguous_binding_cannot_choose_only_numeric_candidate(self):
        text="我又买了一台机器"
        old=memory(fact("数量","2台",evidence="我有2台打印机",subject="用户的打印机"))
        raw=extract(fact("设备","增加一台机器",evidence=text))
        raw["facts"][0].update(intent="relative",delta=1)
        result,_,_,writer=await self.run_turn(text,[raw],memories=[old])
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])

    async def test_invalid_binding_id_or_unit_is_rejected(self):
        for target,unit in ((999,"台"),(True,"台"),(11,"岁")):
            with self.subTest(target=target,unit=unit):
                text="我又买了一台打印机"
                old=memory(fact("数量","2台",evidence="我有2台打印机",subject="用户的打印机"))
                raw=extract(fact("设备","增加一台打印机",evidence=text))
                raw["facts"][0].update(intent="relative",delta=1)
                result,_,_,writer=await self.run_turn(text,[raw],
                    {QUANTITY_BIND_PROMPT:[{"targetId":target,"sameQuantity":True,"unit":unit}]},memories=[old])
                self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])

    async def test_binding_still_requires_independent_semantic_review(self):
        text="我又买了一台打印机"
        old=memory(fact("数量","2台",evidence="我有2台打印机",subject="用户的打印机"))
        raw=extract(fact("设备","增加一台打印机",evidence=text))
        raw["facts"][0].update(intent="relative",delta=1)
        result,_,_,writer=await self.run_turn(text,[raw,review(subjectSupported=False)],
            {QUANTITY_BIND_PROMPT:[{"targetId":11,"sameQuantity":True,"unit":"台"}]},memories=[old])
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])

    async def test_explicit_unit_cannot_bind_quantity_change_to_age(self):
        text="我又买了一台打印机"
        old=memory(fact("年龄","21岁",evidence="我21岁"))
        raw=extract(fact("设备","增加一台打印机",evidence=text))
        raw["facts"][0].update(intent="relative",delta=1)
        result,_,_,writer=await self.run_turn(text,[raw],
            {QUANTITY_BIND_PROMPT:[{"targetId":11,"sameQuantity":True,"unit":"岁"}]},memories=[old])
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])

    async def test_historical_quantity_never_binds_to_current_state(self):
        text="去年我多买了一台打印机"
        old=memory(fact("数量","2台",evidence="我有2台打印机",subject="用户的打印机"))
        raw=extract(fact("设备","增加一台打印机","historical",text))
        raw["facts"][0].update(intent="relative",delta=1)
        result,llm,_,_=await self.run_turn(text,[raw,{"action":"none"}],memories=[old])
        self.assertEqual([],result)
        self.assertNotIn(QUANTITY_BIND_PROMPT,[s for s,_ in llm.all_calls])


if __name__=="__main__": unittest.main()
