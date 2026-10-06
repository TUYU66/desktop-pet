"""管理结构回归：补漏、对象目录、属性同义和一次定向修复，不访问真实记忆。"""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from test_memory import Llm, MODULE, WHEN, fact, memory, extract, align, review, FaultInjectedProvider
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
from core.providers.memory.mem_local_short.management import (
    COVERAGE_PROMPT, RECENT_PROMPT, RESOLVE_PROMPT, REPAIR_PROMPT, Registry,
)
from core.providers.memory.mem_local_short.facts import digest, normalize, fact_key


class ScriptedLlm(Llm):
    def __init__(self, responses, phases):
        super().__init__(*responses)
        self.phases = {key: list(values) for key, values in phases.items()}
        self.all_calls = []

    def response_no_stream(self, system, data, **kwargs):
        payload = json.loads(data)
        self.all_calls.append((system, payload))
        queue = self.phases.get(system)
        if queue:
            result = queue.pop(0)
            if callable(result): result = result(payload)
            return json.dumps(result, ensure_ascii=False)
        return super().response_no_stream(system, data, **kwargs)


def resolution(subject="user", predicate="年龄", code="age", **extra):
    return dict(ambiguous=False, resolvedSubject=subject, entityId=None, predicateId=None,
                predicateLabel=predicate, canonicalPredicate=code,
                predicateDefinition="该对象的“"+predicate+"”属性，不包含其他属性",
                relationToPreviousFact="explicit", **extra)


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, text, responses, phases=None, memories=(), context=(), production=False):
        llm = ScriptedLlm(responses, phases or {})
        provider = (MemoryProvider if production else FaultInjectedProvider)({"memory_policy":"legacy"}); provider.init_memory("default", llm)
        loader = AsyncMock(return_value=dict(schemaVersion=2, roleId="default", revision=5,
                                            memories=list(memories)))
        async def commit(*args):
            return {name:sum(op["action"]==action for op in args[-1])
                    for name,action in (("created","add"),("updated","update"),("deleted","delete"))}
        writer = AsyncMock(side_effect=commit)
        with patch(MODULE+".search_memory_facts", loader), patch(MODULE+".commit_memory_facts", writer):
            result = await provider.save_memory(
                [NS(role="user",content=text,created_at=WHEN,uniq_id="test-turn")],
                "test-session", user_context=context)
        return result, llm, loader, writer

    async def test_whole_sentence_evidence_does_not_hide_missing_age(self):
        text="我是大学生，今年21岁"
        student=fact("学籍","大学生",evidence=text); age=fact(evidence="今年21岁")
        result,llm,_,writer=await self.run_turn(text,
            [extract(student),align(student),review(),align(age),review()],
            {COVERAGE_PROMPT:[{"missingFacts":extract(age)["facts"]}]})
        self.assertEqual({"学籍","年龄"},{op["fact"]["predicate"] for op in result})
        covered=next(data for system,data in llm.all_calls if system==COVERAGE_PROMPT)
        self.assertEqual("大学生",covered["candidateFacts"][0]["value"])
        self.assertNotIn("sourceEvidence",covered["candidateFacts"][0])
        self.assertEqual(5,writer.call_args.args[3])

    async def test_coverage_cannot_rewrite_existing_candidate_or_reuse_old_quote(self):
        text="我21岁"; invented=fact("职业","护士",evidence="我是护士")
        result,_,_,_=await self.run_turn(text,[extract(fact()),align(fact()),review()],
            {COVERAGE_PROMPT:[{"missingFacts":extract(fact(),invented)["facts"]}]})
        self.assertEqual(1,len(result)); self.assertEqual("21岁",result[0]["fact"]["value"])

    async def test_empty_extraction_can_be_filled_by_coverage(self):
        result,_,_,_=await self.run_turn("我21岁",[extract(),align(fact()),review()],
            {COVERAGE_PROMPT:[{"missingFacts":extract(fact())["facts"]}]})
        self.assertEqual(1,len(result))

    async def test_over_budget_coverage_fails_closed_before_database(self):
        facts=[fact("属性"+str(i),"21",evidence="我21岁") for i in range(7)]
        result,_,loader,writer=await self.run_turn("我21岁",[extract(*facts[:6])],
            {COVERAGE_PROMPT:[{"missingFacts":extract(facts[6])["facts"]}]})
        self.assertIsNone(result); loader.assert_not_awaited(); writer.assert_not_awaited()

    async def test_ambiguous_object_is_not_forced_to_latest_entity(self):
        text="在市场买的"; new=fact("购买来源","市场",evidence=text,subject="物品")
        context=[NS(role="user",content="我的自行车和姐姐的汽车",created_at=WHEN)]
        recent={"entities":[dict(name=name,aliases=[],messageIndex=0,evidence="我的自行车和姐姐的汽车")
                             for name in ("用户的自行车","姐姐的汽车")]}
        result,llm,_,_=await self.run_turn(text,[extract(new)],
            {RECENT_PROMPT:[recent],RESOLVE_PROMPT:[{"ambiguous":True}]},context=context)
        self.assertEqual([],result)
        data=next(data for system,data in llm.all_calls if system==RESOLVE_PROMPT)
        self.assertEqual(2,len(data["recentEntities"]))
        self.assertFalse(any(system==REPAIR_PROMPT for system,_ in llm.all_calls))

    async def test_context_resolution_stores_actual_user_provenance(self):
        text="在市场买的"; new=fact("购买来源","市场",evidence=text,subject="物品")
        quote="我的自行车是红色的"; name="用户的自行车"
        result,_,_,_=await self.run_turn(text,[extract(new),align(new),review()],
            {RECENT_PROMPT:[{"entities":[dict(name=name,aliases=[],messageIndex=0,evidence=quote)]}],
             RESOLVE_PROMPT:[resolution(name,"购买来源","purchase_source",
                 contextIndex=0,contextEvidence=quote)|{"relationToPreviousFact":"context"}]},
            context=[NS(role="user",content=quote,created_at=WHEN,uniq_id="context-1")])
        saved=result[0]["fact"]
        self.assertEqual(name,saved["subject"])
        self.assertEqual("context-1",saved["contextMessageId"])
        self.assertEqual(quote,saved["contextEvidence"])

    async def test_fabricated_context_quote_does_not_pass_even_with_valid_id(self):
        text="在市场买的"; new=fact("购买来源","市场",evidence=text,subject="物品")
        result,_,_,_=await self.run_turn(text,[extract(new)],
            {RESOLVE_PROMPT:[resolution("用户的自行车","购买来源","purchase_source",
                contextIndex=0,contextEvidence="我的自行车")|{"relationToPreviousFact":"context"}, {"ambiguous":True}]},
            context=[NS(role="user",content="我有汽车",created_at=WHEN)])
        self.assertEqual([],result)

    async def test_predicate_alias_reuses_stable_id_for_update(self):
        text="我的车在哪里买的？不对，是在二手市场买的"
        old=memory(fact("购买来源","商店",evidence="我的车在商店买的",subject="用户的车"))
        catalog=Registry([old]); eid=next(e for e in catalog.entities if e!="user")
        pid=next(iter(catalog.predicates)); code=catalog.predicates[pid]["code"]
        new=fact("购买渠道","二手市场",evidence="是在二手市场买的",subject="用户的车")
        r=resolution("用户的车","购买来源",code); r.update(entityId=eid,predicateId=pid)
        result,_,_,_=await self.run_turn(text,
            [extract(new),align(new,"update",11,"changed"),review()],
            {RESOLVE_PROMPT:[r]},[old])
        saved=result[0]["fact"]
        self.assertEqual(pid,saved["predicateId"]); self.assertEqual(old["factKey"],fact_key(saved))
        self.assertIn("购买渠道",saved["predicateAliases"])

    async def test_purchase_location_is_not_merged_into_purchase_source(self):
        text="我的车在上海购买"; new=fact("购买地点","上海",evidence=text,subject="用户的车")
        old=memory(fact("购买来源","商店",evidence="我的车在商店买的",subject="用户的车"))
        result,_,_,_=await self.run_turn(text,[extract(new),align(new),review()],
            {RESOLVE_PROMPT:[resolution("用户的车","购买地点","purchase_location")]},[old])
        self.assertEqual("add",result[0]["action"])
        self.assertNotEqual(old["factKey"],fact_key(result[0]["fact"]))

    async def test_unknown_directory_ids_are_rejected(self):
        r=resolution(); r["predicateId"]="p_unknown"
        result,_,_,_=await self.run_turn("我21岁",[extract(fact())],{RESOLVE_PROMPT:[r,r]})
        self.assertEqual([],result)

    async def test_numeric_error_is_repaired_once_then_hard_checked_and_audited(self):
        text="我又多了一辆自行车"; subject="用户的自行车"
        old=memory(fact("数量","两辆",evidence="我有两辆自行车",subject=subject))
        source=fact("数量","增加一辆",evidence=text,subject=subject)
        payload=extract(source); payload["facts"][0].update(intent="relative",delta=1)
        bad=align(source|{"value":"一辆"},"update",11,"changed")
        good=align(source|{"value":"三辆"},"update",11,"changed")
        result,llm,_,_=await self.run_turn(text,[payload,bad,review()],
            {REPAIR_PROMPT:[{"operation":good,"correctedSource":None}]},[old])
        self.assertEqual("三辆",result[0]["fact"]["value"])
        self.assertEqual(1,sum(system==REPAIR_PROMPT for system,_ in llm.all_calls))
        audit=llm.calls[-1][1]
        self.assertEqual("from_snapshot",audit["hardChecks"]["targetVersion"])
        self.assertEqual(2,result[0]["expectedVersion"])

    async def test_failed_numeric_repair_is_not_retried_or_sent_to_audit(self):
        text="我又多了一辆自行车"; subject="用户的自行车"
        old=memory(fact("数量","两辆",evidence="我有两辆自行车",subject=subject))
        source=fact("数量","增加一辆",evidence=text,subject=subject)
        payload=extract(source); payload["facts"][0].update(intent="relative",delta=1)
        bad=align(source|{"value":"一辆"},"update",11,"changed")
        result,llm,_,_=await self.run_turn(text,[payload,bad],
            {REPAIR_PROMPT:[{"operation":bad,"correctedSource":None}]},[old])
        self.assertEqual([],result); self.assertEqual(2,len(llm.calls))
        self.assertEqual(1,sum(system==REPAIR_PROMPT for system,_ in llm.all_calls))

    async def test_time_conflict_repair_adds_history_without_overwriting_current(self):
        text="去年我住在上海"; past=fact("住所","上海","historical",text)
        old=memory(fact("住所","北京",evidence="我现在住在北京"))
        result,llm,_,_=await self.run_turn(text,
            [extract(past),align(past,"update",11,"changed","event"),review()],
            {REPAIR_PROMPT:[{"operation":align(past,category="event"),"correctedSource":None}]},[old])
        self.assertEqual("add",result[0]["action"])
        self.assertEqual("historical",result[0]["fact"]["temporalScope"])
        self.assertEqual("2025",result[0]["fact"]["timeValue"])
        self.assertEqual(1,sum(system==REPAIR_PROMPT for system,_ in llm.all_calls))

    async def test_semantic_denial_cannot_be_repaired_until_approved(self):
        result,llm,_,_=await self.run_turn("我21岁",
            [extract(fact()),align(fact()),review(subjectSupported=False)])
        self.assertEqual([],result)
        self.assertFalse(any(system==REPAIR_PROMPT for system,_ in llm.all_calls))

    async def test_sensitive_fragments_are_redacted_in_every_new_phase(self):
        text="我的密码是abc123，我21岁"; context=[NS(role="user",content="access token: xyz",created_at=WHEN)]
        _,llm,_,_=await self.run_turn(text,[extract(fact()),align(fact()),review()],context=context)
        self.assertNotIn("abc123",str(llm.all_calls)); self.assertNotIn("xyz",str(llm.all_calls))
        self.assertTrue({COVERAGE_PROMPT,RECENT_PROMPT,RESOLVE_PROMPT}.issubset({s for s,_ in llm.all_calls}))

    async def test_id_key_is_stable_across_display_labels(self):
        f=fact()|{"entityId":"user","predicateId":"p_age"}
        self.assertEqual(fact_key(f),fact_key(f|{"predicate":"今年多大"}))
        self.assertEqual(digest("user\x1fp_age"),fact_key(f))


if __name__=="__main__": unittest.main()
