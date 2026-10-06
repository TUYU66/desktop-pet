"""类别隔离及历史ID兼容；模型与数据库均使用桩，需手动运行。"""
import unittest
from unittest.mock import AsyncMock

from test_memory import WHEN
from test_memory_categories import classified
import test_memory_policy as support
from test_memory_policy import record
from test_memory_management import resolution
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
from core.providers.memory.mem_local_short.categories import classified_source
from core.providers.memory.mem_local_short.management import Registry


class CategoryIdentityTests(unittest.IsolatedAsyncioTestCase):
    run_turn=support.PolicyTests.run_turn

    async def test_same_label_in_different_categories_creates_separate_slots(self):
        old,_,_=await self.run_turn("我每天跑步",[classified("跑步","每天跑步","habit")])
        ops,_,_=await self.run_turn("我喜欢跑步",[classified("跑步","喜欢跑步","preference")],
                                    [record(old[0])])
        self.assertEqual("add",ops[0]["action"])
        self.assertNotEqual(old[0]["fact"]["predicateId"],ops[0]["fact"]["predicateId"])
        self.assertEqual(old[0]["fact"]["entityId"],ops[0]["fact"]["entityId"])

    async def test_same_category_reuses_historical_unscoped_id(self):
        first,_,_=await self.run_turn("我每天跑步",[classified("跑步","每天跑步","habit")])
        first[0]["fact"].update(predicateId="p_legacy_running",canonicalPredicate="running")
        ops,_,_=await self.run_turn("我每周跑步",[classified("跑步","每周跑步","habit")],
                                    [record(first[0])])
        self.assertEqual("update",ops[0]["action"])
        self.assertEqual("p_legacy_running",ops[0]["fact"]["predicateId"])

    async def test_alias_is_reused_only_inside_its_category(self):
        first,_,_=await self.run_turn("我每天跑步",[classified("跑步习惯","每天跑步","habit")])
        first[0]["fact"]["predicateAliases"]=["跑步"]
        memories=[record(first[0])]
        same,_,_=await self.run_turn("我每周跑步",[classified("跑步","每周跑步","habit")],memories)
        other,_,_=await self.run_turn("我喜欢跑步",[classified("跑步","喜欢跑步","preference")],memories)
        self.assertEqual(first[0]["fact"]["predicateId"],same[0]["fact"]["predicateId"])
        self.assertNotEqual(first[0]["fact"]["predicateId"],other[0]["fact"]["predicateId"])

    async def test_resolver_cannot_choose_predicate_outside_category(self):
        old,_,_=await self.run_turn("我每天跑步",[classified("跑步","每天跑步","habit")])
        provider=MemoryProvider({})
        base=dict(latestUser="小王喜欢跑步",userContext=[],recentEntities=[])
        raw={**classified("跑步","喜欢跑步","preference"),"subject":"朋友小王","evidence":base["latestUser"]}
        source=classified_source(provider,raw,base,WHEN.date(),[record(old[0])])
        answer=resolution("朋友小王","跑步","running")
        answer["predicateId"]=old[0]["fact"]["predicateId"]
        provider._ask_json=AsyncMock(return_value=answer)
        with self.assertRaisesRegex(ValueError,"unknown_predicate_id"):
            await provider._canonicalize_once(source,base,Registry([record(old[0])],"preference"),WHEN.date())

    async def test_non_user_resolver_generates_category_scoped_ids(self):
        provider=MemoryProvider({})
        ids=[]
        for category in ("habit","preference","goal"):
            base=dict(latestUser="小王谈起跑步",userContext=[],recentEntities=[])
            raw={**classified("跑步","跑步",category),"subject":"朋友小王","evidence":base["latestUser"]}
            source=classified_source(provider,raw,base,WHEN.date(),[])
            provider._ask_json=AsyncMock(return_value=resolution("朋友小王","跑步","running"))
            canonical=await provider._canonicalize_once(source,base,Registry([],category),WHEN.date())
            ids.append(canonical["fact"]["predicateId"])
        self.assertEqual(3,len(set(ids)))


if __name__=="__main__": unittest.main()
