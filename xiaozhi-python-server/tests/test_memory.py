"""事实级记忆回归，云端模型及数据库均隔离。"""
import json
import unittest
from datetime import datetime,date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider,format_memories,MAX_PROMPT_CHARS
from core.providers.memory.mem_local_short.facts import (CHECKS,redact,redact_tree,validate_fact,render_fact,fact_key,audit_approved,evidence_matches,check_numeric_transition)

MODULE="core.providers.memory.mem_local_short.mem_local_short"
WHEN=datetime(2026,9,13,10,0)

class FaultInjectedProvider(MemoryProvider):
    """强制送入模型错误提案，专门验证硬门禁；生产快路径另有独立测试。"""
    def __init__(self,config): super().__init__({**config,"memory_policy":"legacy"})
    def _direct_resolution(self, source, registry, base): return None
    async def _plan_operation(self, source, data, memories):
        return await self._ask_json(self.align_prompt,data)

def fact(predicate="年龄",value="21岁",scope="current",evidence="我21岁",subject="user",time="2025",precision="year"):
    return dict(subject=subject,predicate=predicate,value=value,temporalScope=scope,evidence=evidence,timePrecision=precision,timeValue=time)

def extract(*facts): return {"facts":[{**f,"category":"event" if f["temporalScope"]=="historical" else "profile","intent":"assert"} for f in facts]}
def align(f,action="add",id=None,relation="new",category="profile"):
    return dict(action=action,id=id,relation=relation,fact=f,category=category)
def review(**changes): return {**{k:True for k in CHECKS},**changes}
def memory(f,id=11,category="profile",version=2):
    f=validate_fact(f,f["evidence"],WHEN.date())
    return dict(id=id,category=category,version=version,factKey=fact_key(f),factJson=json.dumps(f,ensure_ascii=False),content=render_fact(f))

class Llm:
    def __init__(self,*responses): self.responses=list(responses); self.calls=[]
    def response_no_stream(self,system,data,**kwargs):
        payload=json.loads(data)
        from core.providers.memory.mem_local_short.protocol import EXTRACTION_REVIEW_PROMPT
        if system==EXTRACTION_REVIEW_PROMPT: return json.dumps({"facts":payload["candidates"]},ensure_ascii=False)
        from core.providers.memory.mem_local_short.management import RECENT_PROMPT,COVERAGE_PROMPT,RESOLVE_PROMPT,REPAIR_PROMPT,SOURCE_REPAIR_PROMPT,QUANTITY_BIND_PROMPT,ENTITY_BIND_PROMPT
        if system==RECENT_PROMPT: return json.dumps({"entities":[]})
        if system==COVERAGE_PROMPT: return json.dumps({"missingFacts":[]})
        if system==REPAIR_PROMPT: return json.dumps({"operation":{"action":"none"},"correctedSource":None})
        if system==SOURCE_REPAIR_PROMPT: return json.dumps({"correctedSource":None})
        if system==QUANTITY_BIND_PROMPT: return json.dumps({"targetId":None,"sameQuantity":False,"unit":""})
        if system==ENTITY_BIND_PROMPT: return json.dumps({"bindings":[]})
        if system==RESOLVE_PROMPT:
            source=payload["sourceFact"]
            entity=next((e for e in payload["entityCatalog"] if e["name"]==source["subject"]),None)
            predicate=next((p for p in payload["predicateCatalog"] if p["label"]==source["predicate"]),None)
            return json.dumps(dict(ambiguous=False,resolvedSubject=source["subject"],entityId=entity["id"] if entity else None,
                predicateId=predicate["id"] if predicate else None,canonicalPredicate=predicate["code"] if predicate else "attr_"+__import__('hashlib').sha256(source["predicate"].encode()).hexdigest()[:16],
                predicateDefinition="只管理这一独立属性",predicateLabel=source["predicate"],relationToPreviousFact="explicit"),ensure_ascii=False)
        self.calls.append((system,payload))
        response=self.responses.pop(0)
        if isinstance(response,Exception): raise response
        return response if isinstance(response,str) else json.dumps(response,ensure_ascii=False)

class FactProtocolTests(unittest.TestCase):
    def test_json_redaction_cannot_corrupt_structure(self):
        tree={"value":"密码是abc123","sourceEvidence":"我说密码是abc123"}
        self.assertEqual({"value":"[REDACTED]","sourceEvidence":"我说[REDACTED]"},redact_tree(tree))

    def test_undated_long_term_future_is_representable(self):
        f=validate_fact(fact("学习目标","学习摄影","future","我准备长期学习摄影",time="未定日期",precision="approximate"),"我准备长期学习摄影",WHEN.date())
        self.assertEqual("future",f["temporalScope"])
    def test_credential_redaction_preserves_other_facts(self):
        value=redact("密码是abc123，我21岁，access token: xyz，手机13800138000，邮箱a@b.com")
        for secret in ("abc123","xyz","13800138000","a@b.com"): self.assertNotIn(secret,value)
        self.assertIn("我21岁",value)

    def test_generic_json_fact_has_separate_attribute_and_date(self):
        parsed=validate_fact(fact(),"我21岁",WHEN.date())
        self.assertEqual("2026-09-13",parsed["timeValue"])
        self.assertEqual("21岁",parsed["value"])
        self.assertIn("截至2026-09-13",render_fact(parsed))

    def test_year_and_vague_history(self):
        for precision,value in (("year","2025"),("approximate","小时候")):
            parsed=validate_fact(fact("住所","上海","historical","我以前住上海",time=value,precision=precision),"我以前住上海",WHEN.date())
            self.assertEqual(value,parsed["timeValue"])

    def test_future_history_and_unresolved_date_are_rejected(self):
        for scope,time,precision in (("historical","2027","year"),("future","2025","year"),("historical","去年","approximate"),("historical","2026-99","month")):
            with self.assertRaises(ValueError): validate_fact(fact(scope=scope,time=time,precision=precision),"我21岁",WHEN.date())

    def test_identity_normalization_is_not_text_overlap(self):
        self.assertEqual(fact_key(fact("年龄")),fact_key(fact(" 年龄 ")))
        self.assertNotEqual(fact_key(fact("年龄")),fact_key(fact("旅行目标")))
        self.assertNotEqual(fact_key(fact(subject="user")),fact_key(fact(subject="用户的猫")))

    def test_evidence_and_audit_fail_closed(self):
        self.assertTrue(evidence_matches("我21岁","我21岁。"))
        self.assertFalse(evidence_matches("用户21岁","我21岁"))
        for key in CHECKS:
            self.assertFalse(audit_approved(review(**{key:False})))
        self.assertFalse(audit_approved({"confidence":0.99}))
        self.assertTrue(audit_approved(review()))

    def test_titles_are_not_rewritten(self):
        f=validate_fact(fact("歌曲偏好","《明天会更好》",evidence="我喜欢《明天会更好》"),"我喜欢《明天会更好》",WHEN.date())
        self.assertIn("《明天会更好》",render_fact(f))

    def test_format_budget(self):
        result=format_memories([{"content":"abc"},{"content":"def"}],max_chars=6)
        self.assertIn("abc",result); self.assertNotIn("def",result); self.assertIn("仅展示部分",result)

class MemoryProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_alignment_cannot_replace_new_fact_with_another_sentence_fact(self):
        text="我21岁大三了"; age=fact(evidence=text); grade=fact("年级","大学三年级",evidence=text)
        result,_,llm,_,_=await self.run_turn(text,[extract(grade),align(age),review()])
        self.assertEqual("年级",result[0]["fact"]["predicate"])
        self.assertEqual("大学三年级",llm.calls[2][1]["proposedOperation"]["fact"]["value"])

    async def test_add_completed_relation_is_rejected_not_silently_kept_as_goal(self):
        result,*_=await self.run_turn("我21岁",[extract(fact()),align(fact(),relation="completed")])
        self.assertEqual([],result)

    async def test_delta_without_existing_current_target_is_not_stored_as_total(self):
        source=fact("数量","增加一辆",evidence="我又多了一辆车",subject="用户的车")
        payload=extract(source); payload["facts"][0]["intent"]="relative"
        result,*_=await self.run_turn("我又多了一辆车",[payload,align(source)])
        self.assertEqual([],result)

    async def test_goal_entity_is_included_in_retrieval_terms(self):
        source=fact("旅行经历","日本","historical","我去过日本",time="未注明日期的过往",precision="approximate")
        _,_,_,loader,_=await self.run_turn("我去过日本",[extract(source),{"action":"none"}])
        self.assertIn("日本",loader.call_args.args[2])
    async def test_uninitialized_provider_and_synthetic_user_are_not_sources(self):
        self.assertIsNone(await MemoryProvider({}).save_memory([NS(role="user",content="我21岁")]))
        result,_,llm,_,_=await self.run_turn("",[],msgs=[NS(role="user",content="系统提示",is_user_input=False)])
        self.assertIsNone(result); self.assertEqual([],llm.calls)

    async def test_wrong_role_snapshot_does_not_leak_into_answers(self):
        provider=MemoryProvider({}); provider.init_memory("default",Llm())
        with patch(MODULE+".search_memory_facts",AsyncMock(return_value=dict(schemaVersion=2,roleId="other",memories=[{"content":"他人事实"}]))):
            answer=await provider.query_memory("你好")
        self.assertIn("读取失败",answer); self.assertNotIn("他人事实",answer)

    async def test_failed_commit_can_be_retried_with_same_turn(self):
        provider=FaultInjectedProvider({}); llm=Llm(extract(fact()),align(fact()),review(),extract(fact()),align(fact()),review()); provider.init_memory("default",llm)
        snapshot=dict(schemaVersion=2,roleId="default",revision=5,memories=[])
        with patch(MODULE+".search_memory_facts",AsyncMock(return_value=snapshot)),patch(MODULE+".commit_memory_facts",AsyncMock(side_effect=[None,{"created":1}])) as writer:
            msgs=[NS(role="user",content="我21岁",created_at=WHEN,uniq_id="retry")]
            self.assertIsNone(await provider.save_memory(msgs,"s")); self.assertTrue(await provider.save_memory(msgs,"s"))
        self.assertEqual(["retry","retry"],[call.args[2] for call in writer.call_args_list])
    async def run_turn(self,text,responses,memories=(),context=(),commit=None,msgs=None):
        provider=FaultInjectedProvider({}); llm=Llm(*responses); provider.init_memory("default",llm)
        loader=AsyncMock(return_value=dict(schemaVersion=2,userId=7,roleId="default",revision=5,memories=list(memories),truncated=False))
        async def successful_commit(*args):
            ops=args[-1]
            return {"created":sum(op["action"]=="add" for op in ops),"updated":sum(op["action"]=="update" for op in ops),"deleted":sum(op["action"]=="delete" for op in ops)}
        writer=AsyncMock(side_effect=successful_commit) if commit is None else AsyncMock(return_value=commit)
        messages=msgs or [NS(role="user",content=text,created_at=WHEN,uniq_id="turn-1")]
        with patch(MODULE+".search_memory_facts",loader),patch(MODULE+".commit_memory_facts",writer):
            result=await provider.save_memory(messages,"s",user_context=context)
        return result,provider,llm,loader,writer

    async def test_age_and_grade_save_separately_without_text_anchor(self):
        text="我今年21岁大三了"
        age= fact(evidence=text); grade=fact("年级","大学三年级",evidence=text)
        result,_,_,_,writer=await self.run_turn(text,[extract(age,grade),align(age),review(),align(grade),review()])
        self.assertEqual(2,len(result)); self.assertEqual({"年龄","年级"},{op["fact"]["predicate"] for op in result})
        self.assertTrue(all(op["fact"]["timeValue"]=="2026-09-13" for op in result))
        self.assertEqual(5,writer.call_args.args[3])

    async def test_history_and_current_in_same_sentence_are_independent(self):
        text="去年大二养了两只猫，现在我21岁"
        history=fact("养猫经历","大二时养了两只猫","historical",text); age=fact(evidence=text)
        result,*_=await self.run_turn(text,[extract(history,age),align(history,category="event"),review(),align(age),review()])
        self.assertEqual(["historical","current"],[op["fact"]["temporalScope"] for op in result])
        self.assertEqual(["2025","2026-09-13"],[op["fact"]["timeValue"] for op in result])

    async def test_market_followup_refines_purchase_not_current_age(self):
        text="对啊我去年大二的时候在市场买的"
        old=fact("购买来源","大二期间购买","historical","去年买了猫",subject="用户的猫")
        new=fact("购买来源","大二期间在市场购买","historical",text,subject="用户的猫")
        age=fact("年龄","两岁",evidence="猫现在两岁",subject="用户的猫")
        prior=[NS(role="user",content="我养的两只猫现在两岁",created_at=WHEN)]
        result,_,llm,_,_=await self.run_turn(text,[extract(new),align(new,"update",11,"refines","event"),review()],[memory(old,category="event"),memory(age,12)],prior)
        self.assertEqual(11,result[0]["id"]); self.assertEqual("2025",result[0]["fact"]["timeValue"])
        self.assertIn("市场",result[0]["fact"]["value"])
        self.assertNotIn("userContext",llm.calls[0][1])
        self.assertIn("猫",str(llm.calls[-1][1]["userContext"]))

    async def test_relative_quantity_uses_current_fact(self):
        text="我加养了一只猫"; old=fact("数量","两只",evidence="我有两只猫",subject="用户的猫")
        source=fact("数量","增加一只",evidence=text,subject="用户的猫"); final={**source,"value":"三只"}
        payload=extract(source); payload["facts"][0].update(intent="relative",delta=1)
        result,*_=await self.run_turn(text,[payload,align(final,"update",11,"changed","relationship"),review()],[memory(old,category="relationship")])
        self.assertEqual("三只",result[0]["fact"]["value"])

    async def test_delta_misread_as_total_is_rejected_before_audit(self):
        text="我又多了一辆车"; old=fact("数量","两辆",evidence="我有两辆车",subject="用户的车")
        source=fact("数量","增加一辆",evidence=text,subject="用户的车")
        payload=extract(source); payload["facts"][0].update(intent="relative",delta=1)
        result,_,llm,_,_=await self.run_turn(text,[payload,align({**source,"value":"一辆"},"update",11,"changed")],[memory(old)])
        self.assertEqual([],result); self.assertEqual(2,len(llm.calls))

    async def test_uncanonical_subject_cannot_overwrite_quantity(self):
        text="我加养了一只猫"; old=fact("数量","两只",evidence="我有两只猫",subject="用户的猫")
        source=fact("宠物","一只猫",evidence=text)
        result,*_=await self.run_turn(text,[extract(source),align({**old,"value":"一只","evidence":text},"update",11,"changed")],[memory(old)])
        self.assertEqual([],result)

    async def test_wrong_target_identity_is_rejected(self):
        text="我21岁"; old=fact("旅行目标","去日本",evidence="我计划去日本")
        result,_,_,_,writer=await self.run_turn(text,[extract(fact()),align(fact(),"update",11,"changed")],[memory(old)])
        self.assertEqual([],result); self.assertEqual([],writer.call_args.args[-1])

    async def test_wrong_time_slot_is_rejected(self):
        text="我去年在上海工作"; old=fact("工作地点","北京",evidence="我现在在北京工作")
        past=fact("工作地点","上海","historical",text)
        result,*_=await self.run_turn(text,[extract(past),align(past,"update",11,"changed")],[memory(old)])
        self.assertEqual([],result)

    async def test_legacy_records_are_not_destructively_migrated(self):
        text="我21岁"; old={"id":11,"version":0,"content":"用户21岁，养了两只猫","category":"profile"}
        result,*_=await self.run_turn(text,[extract(fact()),align(fact(),"update",11,"changed")],[old])
        self.assertEqual([],result)

    async def test_audit_rejects_unrelated_attribute_despite_same_subject(self):
        text="我21岁"
        result,*_=await self.run_turn(text,[extract(fact()),align(fact()),review(attributeSupported=False)])
        self.assertEqual([],result)

    async def test_confidence_does_not_override_audit(self):
        result,*_=await self.run_turn("我21岁",[extract(fact()),align(fact()),{"confidence":0.99}])
        self.assertEqual([],result)

    async def test_new_source_only_even_when_history_is_passed(self):
        msgs=[NS(role="user",content="我20岁",created_at=datetime(2020,1,1)),NS(role="assistant",content="你计划去日本"),NS(role="user",content="我21岁",created_at=WHEN,uniq_id="last")]
        _,_,llm,_,_=await self.run_turn("我21岁",[extract(fact()),align(fact()),review()],msgs=msgs)
        self.assertEqual("我21岁",llm.calls[0][1]["latestUser"]); self.assertIn("2026-09-13",llm.calls[0][1]["referenceTime"])
        self.assertNotIn("日本",str(llm.calls))

    async def test_redaction_happens_before_all_memory_model_calls(self):
        text="我的密码是abc123，我21岁"; context=[NS(role="user",content="access token: xyz",created_at=WHEN)]
        _,_,llm,_,_=await self.run_turn(text,[extract(fact()),align(fact()),review()],context=context)
        self.assertNotIn("abc123",str(llm.calls)); self.assertNotIn("xyz",str(llm.calls)); self.assertIn("21岁",str(llm.calls))

    async def test_no_facts_skip_database_and_followup_models(self):
        result,_,llm,loader,writer=await self.run_turn("我想出门",[extract()])
        self.assertEqual([],result); self.assertEqual(1,len(llm.calls)); loader.assert_not_awaited(); writer.assert_not_awaited()

    async def test_malformed_json_does_not_write(self):
        result,_,_,_,writer=await self.run_turn("我21岁",["not json"])
        self.assertIsNone(result); writer.assert_not_awaited()

    async def test_snapshot_failure_has_no_legacy_write_fallback(self):
        provider=MemoryProvider({}); provider.init_memory("default",Llm(extract(fact())))
        with patch(MODULE+".search_memory_facts",AsyncMock(return_value=None)),patch(MODULE+".commit_memory_facts",AsyncMock()) as writer:
            result=await provider.save_memory([NS(role="user",content="我21岁",created_at=WHEN)])
        self.assertIsNone(result); writer.assert_not_awaited()

    async def test_repeated_turn_id_does_not_reextract(self):
        result,provider,llm,_,_=await self.run_turn("我21岁",[extract(fact()),align(fact()),review()])
        self.assertTrue(result)
        again=await provider.save_memory([NS(role="user",content="我21岁",created_at=WHEN,uniq_id="turn-1")],"s")
        self.assertEqual([],again); self.assertEqual(3,len(llm.calls))

    async def test_query_is_fresh_bounded_and_distinguishes_failure(self):
        provider=MemoryProvider({}); provider.init_memory("default",Llm())
        snapshot=dict(schemaVersion=2,roleId="default",memories=[{"content":"用户21岁"}],truncated=True)
        with patch(MODULE+".search_memory_facts",AsyncMock(side_effect=[snapshot,None])) as loader:
            self.assertIn("用户21岁",await provider.query_memory("你记住了我什么"))
            self.assertTrue(loader.call_args.kwargs["recall"])
            self.assertIn("读取失败",await provider.query_memory("你好"))
        self.assertEqual("",provider.short_memory)

    async def test_overlarge_input_does_not_reach_cloud(self):
        result,_,llm,_,writer=await self.run_turn("字"*2001,[])
        self.assertIsNone(result); self.assertEqual([],llm.calls); writer.assert_not_awaited()

if __name__=="__main__": unittest.main()
