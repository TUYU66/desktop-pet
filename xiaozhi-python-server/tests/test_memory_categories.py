"""大类边界与保存门禁用例；不代表真实模型语义准确率。"""
import unittest
from unittest.mock import AsyncMock
import test_memory_policy as support
from test_memory import WHEN, review
from core.providers.memory.mem_local_short.categories import AUTO_CATEGORIES, classified_source
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
from core.providers.memory.mem_local_short.management import Registry
from core.providers.memory.mem_local_short.policy import custom_source, extraction_prompt
from core.providers.memory.mem_local_short.facts import validate_fact


def classified(predicate, value, category="habit", **extra):
    return support.candidate("classified",value,subject="user",predicate=predicate,category=category,**extra)


class CategoryTests(unittest.IsolatedAsyncioTestCase):
    run_turn = support.PolicyTests.run_turn

    async def test_old_or_invented_field_cannot_enter_automatic_write(self):
        for field in ("age","pet_kind","cat_count","relationship",None):
            with self.subTest(field=field):
                raw=classified("惯用手","左手")
                if field is None: raw.pop("field")
                else: raw["field"]=field
                ops,_,writer=await self.run_turn("我是左撇子",[raw])
                self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_extraction_request_has_categories_without_old_field_catalog(self):
        ops,llm,_=await self.run_turn("我是左撇子",[classified("惯用手","左手")])
        self.assertEqual(1,len(ops))
        self.assertNotIn("coreFields",llm.calls[0][1])
        self.assertNotIn("existingCatalog",llm.calls[0][1])
        self.assertIn("automaticCategories",llm.calls[0][1])

    async def test_old_automatic_labels_are_not_fed_back_into_extraction(self):
        import json
        old,_,_=await self.run_turn("我喜欢西红柿",[classified("西红柿偏好","喜欢西红柿","preference")])
        old[0]["fact"].update(predicate="preference",value="love eating tomatoes",
                              predicateDefinition="old_english_definition")
        _,llm,_=await self.run_turn("我是左撇子，而且我喜欢吃西红柿",[],[support.record(old[0])])
        payload=llm.calls[0][1]
        self.assertNotIn("existingCatalog",payload)
        self.assertNotIn("old_english_definition",json.dumps(payload))
        self.assertNotIn("love eating tomatoes",json.dumps(payload))

    async def test_two_independent_categories_from_same_utterance(self):
        ops,_,writer=await self.run_turn("我是左撇子，而且我喜欢吃西红柿",[
            classified("惯用手","左手","habit"),
            classified("西红柿偏好","喜欢吃西红柿","preference")])
        self.assertEqual({"habit","preference"},{op["category"] for op in ops})
        self.assertEqual(2,len(ops)); writer.assert_awaited_once()

    async def test_handedness_is_an_open_habit_attribute(self):
        ops,llm,writer=await self.run_turn("我是左撇子",[classified("惯用手","左手")])
        self.assertEqual("habit",ops[0]["category"])
        self.assertEqual("惯用手",ops[0]["fact"]["predicate"])
        self.assertEqual("automatic",ops[0]["fact"]["memoryMode"])
        self.assertEqual(2,len(llm.calls))
        writer.assert_awaited_once()

    async def test_handedness_cannot_be_assigned_to_student_status(self):
        ops,_,writer=await self.run_turn("我是左撇子",[classified("学籍","左撇子","profile")],approved=False)
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_open_attribute_still_requires_semantic_approval(self):
        ops,_,writer=await self.run_turn("假如我是左撇子",[classified("惯用手","左手")],approved=False)
        self.assertEqual([],ops); writer.assert_not_awaited()

    async def test_same_attribute_reuses_existing_id(self):
        first,_,_=await self.run_turn("我是左撇子",[classified("惯用手","左手")])
        repeated,_,writer=await self.run_turn("我惯用左手",[classified("惯用手","左手")],[support.record(first[0])])
        self.assertEqual([],repeated); writer.assert_not_awaited()

    async def test_automatic_note_is_blocked_before_audit(self):
        ops,llm,writer=await self.run_turn("我喜欢摄影",[classified("其他","喜欢摄影","note")])
        self.assertEqual([],ops); self.assertEqual(1,len(llm.calls)); writer.assert_not_awaited()

    async def test_note_requires_authorization_and_fallback_reason(self):
        provider=MemoryProvider({})
        raw={"subject":"user","predicate":"备注","value":"纸鹤","category":"note","intent":"assert","temporalScope":"current","evidence":"请记住纸鹤"}
        with self.assertRaises(ValueError):
            custom_source(provider,raw,{"latestUser":"请记住纸鹤"},WHEN.date(),[],True)
        with self.assertRaises(ValueError):
            custom_source(provider,{**raw,"noteReason":"不能归入其他大类"},{"latestUser":"纸鹤"},WHEN.date(),[],False)

    async def test_note_fallback_requires_separate_semantic_approval(self):
        provider=MemoryProvider({})
        text="请记住纸鹤"
        source=classified_source(provider,{"subject":"user","predicate":"约定代号","value":"纸鹤","category":"habit","intent":"assert","temporalScope":"current","evidence":text}, {"latestUser":text},WHEN.date(),[])
        source["category"]="note"
        source["fact"].update(memoryMode="explicit",memoryField="custom")
        source["noteReason"]="用户主动保存的约定代号，不属于其他长期类别"
        base={"latestUser":text,"userContext":[],"recentEntities":[]}
        provider._ask_json=AsyncMock(return_value=review(noteFallback=False))
        result=await provider._process_source(source,base,Registry([]),[],set(),[],WHEN.date())
        self.assertIsNone(result)
        provider._ask_json=AsyncMock(return_value=review(noteFallback=True))
        result=await provider._process_source(source,base,Registry([]),[],set(),[],WHEN.date())
        self.assertEqual("note",result["category"])

    def test_note_is_never_an_automatic_category_or_metadata_value(self):
        self.assertNotIn("note",AUTO_CATEGORIES)
        self.assertIn("note绝不参与自动提取",extraction_prompt(False,[]))
        with self.assertRaises(ValueError):
            validate_fact({"subject":"user","predicate":"备注","value":"纸鹤","evidence":"纸鹤","temporalScope":"current","memoryMode":"automatic","memoryField":"note"},"纸鹤",WHEN.date())
