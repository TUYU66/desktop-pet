"""语义任务与程序门禁分离：实体目录、补漏和有界修复，不维护业务关键词表。"""
import json
import math
import re
from .facts import (
    CATEGORIES, CHECKS, audit_approved, check_numeric_transition, decode_fact,
    digest, entity_id, evidence_matches, fact_key, normalize, predicate_id,
    prompt_memory, redact, same_slot, same_value, validate_fact, numeric_quantity, numeric_mentions,
)

RECENT_PROMPT = """只识别 userContext 中最近谈论的具体对象，不抽取年龄/数量等事实、不读助手背景。
输出 {"entities":[{"name":"稳定对象名称","aliases":["别称"],"messageIndex":0,"evidence":"该条旧用户原话的连续引用"}]}。
最多8个，保留不同对象，不因为最后一个对象就强制所有代词指向它。证据复制对应messageIndex的text。用户本人不用列出。
例如用户先谈自己的自行车再谈姐姐的汽车，是两个对象，不合并。没有明确对象返回entities空数组。"""

COVERAGE_PROMPT = """你只检查最新用户原话的长期事实是否漏提，不关联或修改数据库。
candidateFacts列出已经提取的属性和值；即使evidence引用了整句，也不代表同句所有事实都已经提取。
逐个独立断言检查身份、关系、偏好、习惯、明确长期目标和重要历史经历；即时安排、寒暄、问题不补。
userContext和recentEntities只解代词，不能把旧话补成新事实。不得修改、重复已有候选。
candidateFacts提供属性、值、intent和delta；“拥有某物”不能算覆盖“该物数量增加”的变化断言。
仅输出 {"missingFacts":[完整遗漏事实]}，每项格式：
{"subject":"user或具体对象","predicate":"独立属性","value":"属性值或变化量","category":"profile|preference|relationship|event|goal|habit|note",
"intent":"assert|change|relative|completed|cancelled|corrected|invalidated","temporalScope":"current|historical|future","evidence":"本轮原话连续引用"}。
历史/未来必须有timePrecision/timeValue，相对数量必须intent=relative及有符号数字delta。证据连续复制latestUser。
没有遗漏输出 {"missingFacts":[]}。不能为了补漏猜生日、当前年级、目标完成或未知对象。"""

COVERAGE_PROMPT += """
每个补漏事实使用sourceId引用sourceSegments里的原文片段；证据由程序复制，避免改写。
源断言的历史数量是绝对数量时用assert，不得将“曾持有两件”补成“增加两件”。
"""

RESOLVE_PROMPT = """仅解析sourceFact的对象与属性名称，不改value、证据和时间。
entityCatalog/predicateCatalog来自当前用户角色的已保存事实，ID只选目录已有ID；新对象/属性ID填null。
目录不是必须选择的选项：目录只有数量而新事实是年龄时，predicateId=null，canonicalPredicate=age；禁止把年龄改为数量。
对象不同不能合并：用户本人、用户拥有的物品、他人拥有的物品不同；集合数量与某个个体不同。
关系对象可以是集合，也可以是可识别的个体；集合名称按所有者和成员类别归一，不包含当前数量。
例如用户的猫、用户的狗、用户的姐姐是不同集合；宠物：小花是个体，不能复用用户的猫的entityId。
集合的数量是通用属性，不把成员类别塞进属性代码；不同集合可共用数量predicateId，靠entityId区分。
“其中一只猫叫小花”只识别其中的个体，不代表集合数量减少；“又养了一只猫”关联原猫集合，不新建“新猫集合”。
省略对象使用recentEntities及userContext消解，存在多种合理对象时ambiguous=true，不强选最近一个。
属性同义可以复用目录predicateId，但必须符合definition；购买渠道、购买地点、购买对象不同，不一律合并。
当前类别的属性目录已经由程序筛选；即使userContext或其他资料出现同名属性，也不能引用目录之外的predicateId。相同对象可跨类别存在，不代表其属性可以跨类别合并。
新属性给canonicalPredicate英文snake_case代码和清晰predicateDefinition，不限领域，不要用笼统的喜欢/情况。
输出 {"ambiguous":false,"resolvedSubject":"标准对象名称，用户本人固定user","entityId":null,
"predicateId":null,"canonicalPredicate":"属性英文代码","predicateDefinition":"属性的含义与边界",
"predicateLabel":"中文属性名","relationToPreviousFact":"explicit"}。
用户本人直接自述是explicit！userContext为空时只允许explicit，不能把latestUser伪造为旧上下文。
只有确实借助旧用户原话解释省略对象时用context，额外提供contextIndex和contextEvidence；禁止填写本轮原话。
已有ID采用目录name/label，不重命名。
上下文里的对象名称可能包含数量等修饰语；例如“两台打印机”可对应目录“用户的打印机”，但不能把修饰语作为新数量。
只要标准名称/属性与目录相同，就返回该目录的entityId/predicateId，不要填null新建重复记录。
原话的购买地点是市场，不等于买了市场；上下文讨论车辆后补“在市场买的”，解析的是车辆购买来源，不能重新提取旧车辆年龄。"""

QUANTITY_BIND_PROMPT = """只判断本轮数量增减属于哪个已有的数量状态，不提取新事实、不计算总量。
sourceFact的subject/predicate可能过于笼统，必须回到latestUser的具体对象；recentEntities/userContext只解代词。
targets是当前状态候选，包括其他数值属性，年龄、重量等不能因为也是数字就当作持有数量。
用户自己的集合、他人的集合和单个物品不能混用。只有对象、所有者、集合和数量属性都唯一相符才选targetId。
例如原话“我又买了一台打印机”，旧“用户的打印机/数量/2台”可以关联，旧打印机年龄不能关联。
对象不明确、多个候选均合理、没有对应状态时返回null；不能强行选择唯一一个候选。
只输出 {"targetId":整数ID或null,"sameQuantity":true或false,"unit":"目标数值的单位"}。
不返回correctedSource或operation，不改变原话的delta、证据或时间。"""

ENTITY_BIND_PROMPT = """只把近期谈论对象连接到已保存对象目录，不提取/修改事实或属性。
recentEntities附有真实旧用户原话。对象名称中的数量/颜色等修饰语不是对象身份：
例如旧原话“我有两台打印机”，近期名称“两台打印机”，可对应目录“用户的打印机”。
必须是同一所有者、同一对象/集合；他人的设备不能连接为用户自己的设备；集合不能连接到单个个体。
没有唯一对应或对象不同，entityId=null；不能因为目录只有一个就选它。
只输出 {"bindings":[{"recentIndex":数组索引整数,"entityId":"目录ID或null"}]}。
ID只能来自entityCatalog；不生成名称、证据、属性和值。"""

REPAIR_PROMPT = """只修复指定error的一条事实/关联操作，最多这一次。不要补其他事实，不重新执行整段历史。
原话latestUser、原话时刻referenceTime固定，敏感值、凭空证据、审查否决不能通过修复绕过。
数值错误重新区分绝对值/有符号delta，并用目标当前旧值推导总量；无法唯一计算输出operation.action=none。
时间槽冲突不能把历史覆盖当前：判断是否应historical add；不能为通过门禁抹掉时间。
sourceFact本身对象/属性正确时保留它；如必须纠正数值意图或时间，correctedSource只给这一条完整提取格式，证据仍复制本轮原话。
仅输出 {"operation":完整add/update/delete/none提案,"correctedSource":null或修正后的单条提取事实}。
correctedSource是平铺对象，不包fact：subject,predicate,value,category,intent,temporalScope,evidence，历史/未来另带timePrecision/timeValue，相对数量另带delta。
operation必须是对象，不是字符串；不需关联操作时填 {"action":"none"}。intent不是relative却带delta是格式冲突，重判原话的增减含义，不能把最终值填成变化量。
修正不是批准，所有程序门禁和独立语义审查随后重新执行。"""

SOURCE_REPAIR_PROMPT = """只重新提取指定sourceFact所对应的一条用户断言，修复error，不关联数据库、不求最终总量。
latestUser是唯一新值来源；userContext只解对象；referenceTime固定日历。绝不能从旧原话提取新事实。
数量变化只给变化量：intent=relative、delta=有符号数字，value描述增加/减少量；无需旧值也能提取delta，不要因未知总数返回空。
delta仅用于relative；intent=assert带delta时重判原话是陈述绝对状态还是表达增减。不自动把delta当最终value。
只输出 {"correctedSource":单条平铺事实或null}，事实不是fact嵌套对象，不需operation。
事实必填subject,predicate,value,evidence,category,intent,temporalScope；evidence连续复制latestUser。
subject本人user、其他为具体对象；category=profile/preference/relationship/event/goal/habit/note；
intent=assert/change/relative/completed/cancelled/corrected/invalidated；temporalScope=current/historical/future。
历史/未来必填timePrecision=year/month/day/approximate和timeValue，current日期不填；relative另填delta。
不可换对象/属性补其他断言，不编证据，不靠删时间绕过错误，不能确定就correctedSource=null。"""

SOURCE_REPAIR_PROMPT += """
必须返回JSON对象，不要把correctedSource写成中文句子。复制repairTemplate，再修正错误字段。
完整格式示例（只是结构示例，不能复制为用户事实）：
{"correctedSource":{"subject":"用户的自行车","predicate":"数量","value":"增加一辆","evidence":"我又买了一辆自行车","category":"relationship","intent":"relative","temporalScope":"current","delta":1}}
对于带delta的绝对断言，若原话表达增加/减少，保留变化量、改intent=relative；若原话确为总量，移除delta。
不要把过去的拥有数量当作增加量。输入“去年我有两台打印机”只证明历史数量=两台，不证明增加了两台。
修复时间时同时核对这一条断言的intent：陈述拥有/持有的数量用assert；只有明确相对旧值的增减才用relative。
若保留relative，correctedSource必须保留repairTemplate里的delta；不能只补时间却漏掉delta。
"""

REPAIRABLE = {
    "numeric_delta_missing", "numeric_delta_inconsistent", "numeric_absolute_value_unsupported",
    "numeric_transition_unresolved", "numeric_unit_changed", "time_outside_scope",
    "invalid_time_precision", "invalid_time_value", "unresolved_calendar_time",
    "target_time_slot_changed", "source_identity_not_canonical", "target_identity_changed",
    "association_missing", "existing_slot_requires_update", "action_relation_inconsistent",
    "numeric_entity_binding_unresolved",
    "numeric_intent_inconsistent",
    "numeric_delta_value_inconsistent", "numeric_delta_not_in_evidence",
}


class Registry:
    """目录从已保存JSON构造，别名只有随已审查操作提交后才会成为持久知识。"""
    def __init__(self, memories, category=None):
        self.entities = {"user": {"id": "user", "name": "user", "aliases": []}}
        self.predicates = {}
        self.entity_predicates = {}
        for memory in memories:
            fact = decode_fact(memory)
            if not fact: continue
            eid, pid = entity_id(fact), predicate_id(fact)
            self.entities.setdefault(eid, {"id": eid, "name": fact["subject"], "aliases": fact.get("entityAliases", [])})
            # 实体可跨类别复用，但属性目录必须留在当前类别内。
            if category is not None and memory.get("category")!=category: continue
            self.entity_predicates.setdefault(eid,set()).add(pid)
            self.predicates.setdefault(pid, {"id": pid, "label": fact["predicate"],
                "code": fact.get("canonicalPredicate", "attr_"+digest(normalize(fact["predicate"]))[:16]),
                "definition": fact.get("predicateDefinition", "仅管理“"+fact["predicate"]+"”这个独立属性，不包含其他属性"),
                "aliases": fact.get("predicateAliases", [])})

    def learn(self, fact):
        self.entity_predicates.setdefault(entity_id(fact),set()).add(predicate_id(fact))
        self.entities[entity_id(fact)] = {"id": entity_id(fact), "name": fact["subject"], "aliases": fact.get("entityAliases", [])}
        self.predicates[predicate_id(fact)] = {"id": predicate_id(fact), "label": fact["predicate"],
            "code": fact["canonicalPredicate"], "definition": fact["predicateDefinition"], "aliases": fact.get("predicateAliases", [])}


class FactManagement:
    async def _link_recent_entities(self, base, registry):
        recent=[dict(e) for e in base["recentEntities"]]
        catalog=[e for e in registry.entities.values() if e["id"]!="user"]
        if not recent or not catalog: return recent
        unresolved=[]
        for index,item in enumerate(recent):
            names={normalize(item["name"]),*(normalize(a) for a in item["aliases"])}
            matches=[e for e in catalog if names & {normalize(e["name"]),*(normalize(a) for a in e["aliases"])}]
            if len(matches)==1: item["entityId"]=matches[0]["id"]
            else: unresolved.append(index)
        if not unresolved: return recent
        shown=[]; used=0
        for entity in catalog:
            size=len(json.dumps(entity,ensure_ascii=False))
            if used+size>2500: continue
            shown.append(entity); used+=size
        result=await self._ask_json(ENTITY_BIND_PROMPT,{"recentEntities":recent,
            "userContext":base["userContext"],"entityCatalog":shown},tokens=500)
        bindings=result.get("bindings")
        if not isinstance(bindings,list) or len(bindings)>8: raise ValueError("context_bindings_invalid")
        seen=set()
        for binding in bindings:
            if not isinstance(binding,dict): continue
            index,eid=binding.get("recentIndex"),binding.get("entityId")
            if not isinstance(index,int) or isinstance(index,bool) or index not in unresolved: continue
            if index in seen: raise ValueError("context_binding_repeated")
            seen.add(index)
            if eid is None: continue
            if eid not in {e["id"] for e in shown}: raise ValueError("context_binding_unknown_id")
            recent[index]["entityId"]=eid
        return recent

    @staticmethod
    def _source_segments(text):
        """由程序建立原文引用目录；模型选择ID，不再负责复写证据。"""
        segments=[{"id":0,"text":text}]
        for match in re.finditer(r"[^，,。；;！？!?\n]+",text):
            quote=match.group().strip()
            if quote and quote!=text and len(segments)<13:
                segments.append({"id":len(segments),"text":quote})
        return segments

    def _referenced_source(self, raw, base):
        if not isinstance(raw,dict) or "sourceId" not in raw: return raw
        index=raw["sourceId"]
        segments=self._source_segments(base["latestUser"])
        segment=next((s for s in segments if isinstance(index,int)
                      and not isinstance(index,bool) and s["id"]==index),None)
        if segment is None:
            # 仅一个来源时不存在分句选择歧义；后续数值、归属与语义检查仍照常执行。
            if len(segments)!=1: raise ValueError("source_reference_invalid")
            segment=segments[0]
        # sourceId 协议中的证据只由本地原文决定，不采用模型自带的改写引用。
        return {**raw,"sourceId":segment["id"],"evidence":segment["text"]}

    @staticmethod
    def _assertion_spans(raw, text):
        """来源区间仅用于拒绝去重，不作为事实语义获批的依据。

        跨分句引用优先定位包含明确值/数量的唯一分句；无法唯一定位时
        保守保留整个引用。区间相交检查避免通过缩短或加长引用绕过拒绝。
        """
        if not isinstance(raw, dict): return []
        quote = raw.get("evidence", raw.get("sourceEvidence"))
        if not isinstance(quote, str) or not evidence_matches(quote, text): return []
        pieces = list(re.finditer(r"[^，,。；;！？!?\n]+", quote))
        value = raw.get("value", "")
        number = numeric_quantity(value) if isinstance(value, str) else None
        if number is None and isinstance(raw.get("delta"), (int, float)) and not isinstance(raw["delta"], bool):
            from decimal import Decimal
            if math.isfinite(raw["delta"]): number = (abs(Decimal(str(raw["delta"]))), "")
        matches = [piece for piece in pieces if
                   (isinstance(value, str) and value and value in piece.group()) or
                   (number is not None and number[0] in numeric_mentions(piece.group()))]
        local = (matches[0].start(), matches[0].end()) if len(matches) == 1 else (0, len(quote))
        return [(match.start()+local[0], match.start()+local[1])
                for match in re.finditer(re.escape(quote), text)]

    def _repaired_source(self, corrected, original, base, observed):
        if not isinstance(corrected, dict): raise ValueError("source_repair_not_object")
        corrected=self._referenced_source(corrected,base)
        # 提取修复不能偷换另一条正确事实来消费当前断言的重试机会。
        for key in ("subject", "predicate"):
            if corrected.get(key) != original.get(key): raise ValueError("source_repair_identity_changed")
        quote = original.get("evidence", original.get("sourceEvidence"))
        if corrected.get("evidence", corrected.get("sourceEvidence")) != quote:
            raise ValueError("source_repair_evidence_changed")
        if corrected.get("intent")==original.get("intent")=="relative" and "delta" not in corrected:
            corrected={**corrected,"delta":original.get("delta")}
        return self._source(corrected, base, observed, repaired=True)

    async def _source_with_repair(self, raw, base, extraction_base, observed, repair_attempts):
        try:
            return self._source(raw, base, observed)
        except ValueError as error:
            self._diagnose("extract", error, raw)
            if str(error) not in REPAIRABLE: raise
            repair_key=(tuple(self._assertion_spans(raw,base["latestUser"])),
                        normalize(raw.get("subject")),normalize(raw.get("predicate")))
            if repair_key in repair_attempts: raise ValueError("source_repair_budget_exhausted")
            repair_attempts.add(repair_key)
            template = dict(raw)
            if raw.get("temporalScope") in {"historical", "future"}:
                template.setdefault("timePrecision", None)
                template.setdefault("timeValue", None)
            fixed = await self._ask_json(SOURCE_REPAIR_PROMPT, {
                **extraction_base, "error":str(error), "sourceFact":redact_tree_safe(raw),
                "repairTemplate":{"correctedSource":redact_tree_safe(template)},
                "timeInstruction":"历史/未来必须补timePrecision与timeValue：可定位的年份按referenceTime换算；无日期历史用approximate/未注明日期的过往，未定目标用approximate/未定日期。不能改成current。"})
            return self._repaired_source(fixed.get("correctedSource"),raw,base,observed)

    async def _recent_context(self, context):
        if not context: return []
        payload = await self._ask_json(RECENT_PROMPT, {"userContext": context}, tokens=900)
        values = payload.get("entities")
        if not isinstance(values, list) or len(values)>8: raise ValueError("recent_entities_invalid")
        entities = []
        for item in values:
            if not isinstance(item, dict): continue
            index, quote, name = item.get("messageIndex"), item.get("evidence"), item.get("name")
            if not isinstance(index, int) or isinstance(index, bool) or not 0<=index<len(context): continue
            if not isinstance(quote, str) or not evidence_matches(quote, context[index]["text"]): continue
            if not isinstance(name, str) or not normalize(name) or len(name)>80 or redact(name)!=name: continue
            aliases = item.get("aliases", [])
            if not isinstance(aliases,list) or len(aliases)>8: continue
            aliases = [v for v in aliases if isinstance(v,str) and normalize(v) and len(v)<=80 and redact(v)==v]
            entities.append({"name":name,"aliases":aliases,"messageIndex":index,"evidence":quote})
        return entities

    def _source(self, raw, base, observed, repaired=False):
        if not isinstance(raw,dict) or raw.get("category") not in CATEGORIES: raise ValueError("category_invalid")
        fact = validate_fact(raw,base["latestUser"],observed)
        if raw.get("field")=="classified" and raw.get("category")=="relationship":
            from .protocol import has_compound_quantities
            if has_compound_quantities(fact["value"]): raise ValueError("compound_relationship_requires_atomic_facts")
        intent = raw.get("intent","assert")
        if intent not in {"assert","change","relative","completed","cancelled","corrected","invalidated"}: raise ValueError("intent_invalid")
        delta = raw.get("delta")
        if delta is not None and intent!="relative": raise ValueError("numeric_intent_inconsistent")
        if intent=="relative" and (not isinstance(delta,(int,float)) or isinstance(delta,bool) or not math.isfinite(delta)): raise ValueError("numeric_delta_missing")
        from decimal import Decimal
        mentions=numeric_mentions(fact["sourceEvidence"])
        number=numeric_quantity(fact["value"])
        if intent=="relative":
            if abs(Decimal(str(delta))) not in mentions: raise ValueError("numeric_delta_not_in_evidence")
            if number is not None and number[0]!=abs(Decimal(str(delta))): raise ValueError("numeric_delta_value_inconsistent")
        elif any(amount not in mentions for amount in numeric_mentions(fact["value"])):
            raise ValueError("numeric_value_not_in_evidence")
        return {"fact":fact,"originalFact":fact.copy(),"category":self._category(raw["category"],fact),
                "intent":intent,"delta":delta,"repaired":repaired}

    async def _extract_covered(self, base, observed):
        # 提取器只看本轮原话与对象名。完整旧话仅在后续指代解析/审查提供，
        # 避免较弱模型把上下文里的年龄/数量重新提取为本轮新事实。
        extraction_base={"latestUser":base["latestUser"],"referenceTime":base["referenceTime"],
            "recentEntities":[{"name":e["name"],"aliases":e["aliases"]} for e in base["recentEntities"]],
            "sourceSegments":self._source_segments(base["latestUser"])}
        payload = await self._ask_json(self.extract_prompt,extraction_base)
        facts = payload.get("facts")
        if not isinstance(facts,list) or len(facts)>6: raise ValueError("facts_envelope_invalid")
        sources = []; rejected_spans=[]; attempted=set(); repair_attempts=set()
        for raw in facts:
            try: raw=self._referenced_source(raw,base)
            except ValueError as error:
                self._diagnose("extract_rejected",error); continue
            signature = digest(json.dumps(raw, ensure_ascii=False, sort_keys=True))
            if signature in attempted: continue
            attempted.add(signature)
            try: sources.append(await self._source_with_repair(raw,base,extraction_base,observed,repair_attempts))
            except ValueError as error:
                self._diagnose("extract_rejected",error)
                rejected_spans.extend(self._assertion_spans(raw,base["latestUser"]))
        coverage = await self._ask_json(COVERAGE_PROMPT,{**extraction_base,"candidateFacts":[
            {**{k:s["fact"][k] for k in ("subject","predicate","value","temporalScope")},
             "intent":s["intent"],"delta":s["delta"]} for s in sources],
            "rejectedEvidence":[base["latestUser"][a:b] for a,b in sorted(set(rejected_spans))]})
        missing = coverage.get("missingFacts")
        if not isinstance(missing,list) or len(missing)>6: raise ValueError("coverage_envelope_invalid")
        for raw in missing:
            try:
                raw=self._referenced_source(raw,base)
                spans=self._assertion_spans(raw,base["latestUser"])
                if any(a<d and c<b for a,b in spans for c,d in rejected_spans):
                    raise ValueError("coverage_cannot_rescue_rejected_assertion")
                relative_spans=[span for s in sources if s["intent"]=="relative"
                    for span in self._assertion_spans({**s["fact"],"delta":s["delta"]},base["latestUser"])]
                if isinstance(raw,dict) and raw.get("intent")!="relative" and any(
                        a<d and c<b for a,b in spans for c,d in relative_spans):
                    raise ValueError("coverage_cannot_generalize_relative_assertion")
                signature=digest(json.dumps(raw,ensure_ascii=False,sort_keys=True))
                if signature in attempted: continue
                attempted.add(signature)
                source = await self._source_with_repair(raw,base,extraction_base,observed,repair_attempts)
                if any(same_slot(source["fact"],s["fact"]) and same_value(source["fact"]["value"],s["fact"]["value"]) and source["intent"]==s["intent"] for s in sources): continue
                if len(sources)>=6: raise ValueError("coverage_budget_exceeded")
                sources.append(source)
            except ValueError as error:
                if str(error)=="coverage_budget_exceeded": raise
                self._diagnose("coverage",error,raw)
                if str(error)!="coverage_cannot_rescue_rejected_assertion":
                    rejected_spans.extend(self._assertion_spans(raw,base["latestUser"]))
        return sources

    async def _canonicalize(self, source, base, registry, observed):
        if source.get("controlledField"):
            return {**source,"fact":validate_fact(source["fact"],base["latestUser"],observed)}
        try:
            return await self._canonicalize_once(source,base,registry,observed)
        except ValueError as error:
            recoverable={"context_resolution_evidence_invalid","entity_id_label_mismatch",
                "predicate_id_label_mismatch","unknown_entity_id","unknown_predicate_id","resolution_relation_invalid"}
            if source["repaired"] or str(error) not in recoverable: raise
            self._diagnose("naming_repair",error,source["fact"])
            # 仅重做名称解析，不改值/证据/时间；这一条事实的修复预算同时消耗。
            feedback={"error":str(error),"instruction":"纠正名称解析格式或目录引用；不能改事实，无法确定返回ambiguous=true"}
            return await self._canonicalize_once({**source,"repaired":True},
                {**base,"namingFeedback":feedback},registry,observed)

    def _direct_resolution(self, source, registry, base):
        f=source["fact"]
        matching_entities=[e for e in registry.entities.values() if normalize(f["subject"]) in
            {normalize(e["name"]),*(normalize(a) for a in e["aliases"])}]
        if len(matching_entities)!=1: return None
        entity=matching_entities[0]
        matches=[p for p in registry.predicates.values() if normalize(f["predicate"]) in
            {normalize(p["label"]),*(normalize(a) for a in p["aliases"])}]
        if len(matches)>1: return None
        if source["intent"] == "relative" and not matches: return None
        # 有上下文的物品不能省掉指代解析；没有上下文且对象/属性已精确命中时只沿用目录。
        if entity["id"]!="user" and (base["userContext"] or not matches): return None
        if entity["id"]=="user" and f["subject"]!="user": return None
        relevant=registry.entity_predicates.get(entity["id"],set())
        if not matches and relevant: return None  # 可能是已有属性的同义新说法，交模型判断。
        p=matches[0] if matches else None
        return {"ambiguous":False,"resolvedSubject":entity["name"],"entityId":entity["id"],
            "predicateId":p["id"] if p else None,"predicateLabel":p["label"] if p else f["predicate"],
            "canonicalPredicate":p["code"] if p else "attr_"+digest(normalize(f["predicate"]))[:16],
            "predicateDefinition":p["definition"] if p else "仅管理“"+f["predicate"]+"”这个独立属性，不包含其他属性",
            "relationToPreviousFact":"explicit","method":"exact_name"}

    async def _canonicalize_once(self, source, base, registry, observed):
        catalog={"entityCatalog":[],"predicateCatalog":[]}
        matching_entities=[e["id"] for e in registry.entities.values() if normalize(source["fact"]["subject"]) in
            {normalize(e["name"]),*(normalize(a) for a in e["aliases"])}]
        relevant={pid for eid in matching_entities for pid in registry.entity_predicates.get(eid,set())}
        predicates=[p for p in registry.predicates.values() if not matching_entities or p["id"] in relevant
            or normalize(source["fact"]["predicate"]) in {normalize(p["label"]),*(normalize(a) for a in p["aliases"])}]
        # 两份目录各有预算，避免大量对象挤掉属性目录；原名/别名命中的条目先展示。
        for key,values,label,budget in (("entityCatalog",registry.entities.values(),"subject",1600),
                                       ("predicateCatalog",predicates,"predicate",2400)):
            wanted=normalize(source["fact"][label]); used=0
            ordered=sorted(values,key=lambda item: wanted not in {
                normalize(item.get("name",item.get("label",""))),
                *(normalize(alias) for alias in item.get("aliases",[]))})
            for item in ordered:
                size=len(json.dumps(item,ensure_ascii=False))
                if used+size>budget: continue
                catalog[key].append(item); used+=size
        raw=self._direct_resolution(source,registry,base)
        if raw is None:
            raw = await self._ask_json(RESOLVE_PROMPT,{**base,"sourceFact":source["fact"],
                "allowedRelations":["explicit","context"] if base["userContext"] else ["explicit"],**catalog})
        if raw.get("ambiguous") is not False: raise ValueError("subject_or_predicate_ambiguous")
        original_name=normalize(source["fact"]["subject"])
        linked=[e for e in base["recentEntities"] if e.get("entityId") in registry.entities
                and original_name in {normalize(e["name"]),*(normalize(a) for a in e["aliases"])}]
        if len({e["entityId"] for e in linked})==1:
            cue=linked[0]; entity=registry.entities[cue["entityId"]]
            # 对象名称/ID由已建立的上下文连接确定，证据来自已验证旧原话。
            raw={**raw,"resolvedSubject":entity["name"],"entityId":entity["id"],
                 "relationToPreviousFact":"context","contextIndex":cue["messageIndex"],
                 "contextEvidence":cue["evidence"]}
        name, label, code = raw.get("resolvedSubject"), raw.get("predicateLabel"), raw.get("canonicalPredicate")
        for value in (name,label):
            if not isinstance(value,str) or not normalize(value) or len(value)>80 or redact(value)!=value: raise ValueError("resolution_label_invalid")
        mode = raw.get("relationToPreviousFact")
        context_meta = {}
        if mode=="context":
            index,quote = raw.get("contextIndex"),raw.get("contextEvidence")
            context=base["userContext"]
            if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(context) or not isinstance(quote,str) or not evidence_matches(quote,context[index]["text"]): raise ValueError("context_resolution_evidence_invalid")
            cues=[e for e in base["recentEntities"] if e["messageIndex"]==index]
            contextual_names={normalize(name),normalize(source["fact"]["subject"])}
            if not any(contextual_names & {normalize(e["name"]),*(normalize(a) for a in e["aliases"])} for e in cues): raise ValueError("context_entity_not_in_candidates")
            context_meta={"contextEvidence":quote,"contextMessageId":context[index]["messageId"]}
        elif mode!="explicit": raise ValueError("resolution_relation_invalid")
        eid,pid = raw.get("entityId"),raw.get("predicateId")
        if eid is not None and eid not in {item["id"] for item in catalog["entityCatalog"]}: raise ValueError("unknown_entity_id")
        if pid is not None and pid not in {item["id"] for item in catalog["predicateCatalog"]}: raise ValueError("unknown_predicate_id")
        entity = registry.entities.get(eid)
        predicate = registry.predicates.get(pid)
        # 模型漏填ID但原名精确命中时，从目录确定性补回，不以英文代码猜同义。
        if entity is None and eid is None:
            exact=[e for e in registry.entities.values() if normalize(name)==normalize(e["name"])]
            if len(exact)==1: entity=exact[0]; eid=entity["id"]
        if predicate is None and pid is None and normalize(label)==normalize(source["fact"]["predicate"]):
            exact=[p for p in registry.predicates.values() if normalize(label)==normalize(p["label"])]
            if len(exact)==1: predicate=exact[0]; pid=predicate["id"]
        # 已有ID的标签必须与模型自己给出的标准标签相符，不能用合法数量ID冒充年龄ID。
        if entity and normalize(name)!=normalize(entity["name"]): raise ValueError("entity_id_label_mismatch")
        if predicate and normalize(label)!=normalize(predicate["label"]): raise ValueError("predicate_id_label_mismatch")
        if entity: name=entity["name"]
        else: eid="user" if name=="user" else "e_"+digest(normalize(name))[:40]
        if predicate:
            label,code,definition=predicate["label"],predicate["code"],predicate["definition"]
        else:
            # 没有明确复用已存属性ID，就只能新建提取器给出的独立属性。
            # 名称解析器不能把“职业=大学生”改成“年龄=大学生”，也不能通过
            # 沿用另一属性的英文代码把二者塞入同一事实槽。
            if normalize(label)!=normalize(source["fact"]["predicate"]):
                label=source["fact"]["predicate"]
                code="attr_"+digest(normalize(label))[:16]
                raw={**raw,"predicateLabel":label,"canonicalPredicate":code}
                raw["predicateDefinition"]="仅管理“"+label+"”这个独立属性，不包含其他属性"
            definition=raw.get("predicateDefinition")
            # 新属性的机器代码不需要模型翻译。名称/定义仍须语义审查；
            # 不合法代码只做确定性编码，不将其匹配到其他已有属性。
            # 自动动态属性按名称确定新代码，使同轮不同集合的同名属性共享身份，
            # 不受模型分别翻译为 cat_count / dog_count 等代码影响；已有目录ID优先复用。
            if source["fact"].get("memoryMode") in {"automatic","explicit"} or not isinstance(code,str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,47}",code):
                code="attr_"+digest(normalize(label))[:16]
                if source["fact"].get("memoryMode") in {"automatic","explicit"}:
                    from .categories import dynamic_predicate_code
                    code=dynamic_predicate_code(source["category"],label)
            if not isinstance(definition,str) or not definition.strip() or len(definition)>160 or redact(definition)!=definition: raise ValueError("predicate_definition_invalid")
            pid="p_"+digest(normalize(code))[:40]
            if pid in registry.predicates:
                existing=registry.predicates[pid]
                if normalize(label)!=normalize(existing["label"]):
                    # 未指定目录ID的代码碰撞按新属性编码，不去覆盖目录定义。
                    code="attr_"+digest(normalize(label))[:16]
                    if source["fact"].get("memoryMode") in {"automatic","explicit"}:
                        from .categories import dynamic_predicate_code
                        code=dynamic_predicate_code(source["category"],label)
                    pid="p_"+digest(normalize(code))[:40]
                    existing=registry.predicates.get(pid)
                    if existing and normalize(label)!=normalize(existing["label"]):
                        raise ValueError("predicate_id_label_mismatch")
                if existing:
                    label,code,definition=existing["label"],existing["code"],existing["definition"]
                    predicate=existing
        from .protocol import preferred_label
        label=preferred_label(source["fact"]["predicate"],label)
        fact={**source["fact"],"subject":name,"predicate":label,"entityId":eid,"predicateId":pid,
              "canonicalPredicate":code,"predicateDefinition":definition,**context_meta}
        # 错误提取的user不是物品别名；只把确实出现在原话的对象说法作为候选别名。
        aliases=[name]
        original=source["fact"]["subject"]
        if original!="user" and evidence_matches(original,base["latestUser"]): aliases.append(original)
        fact["entityAliases"]=self._aliases(entity,*aliases)
        fact["predicateAliases"]=self._aliases(predicate,source["fact"]["predicate"],label)
        return {**source,"fact":validate_fact(fact,base["latestUser"],observed),"resolution":raw}

    @staticmethod
    def _aliases(existing, *names):
        return list(dict.fromkeys((existing or {}).get("aliases",[])+list(names)))[:8]

    def _validate_operation(self, source, proposed, memories, related, touched, added, base, observed):
        action = proposed.get("action")
        if action=="none":
            if any((f:=decode_fact(m)) and same_slot(source["fact"],f) and same_value(source["fact"]["value"],f["value"]) for m in memories): return None,None
            raise ValueError("association_missing")
        allowed={"add":{"new"},"update":{"refines","changed","completed","cancelled","corrected"},"delete":{"invalidated"}}
        if action not in allowed: raise ValueError("action_invalid")
        if proposed.get("relation") not in allowed[action]: raise ValueError("action_relation_inconsistent")
        old=next((m for m in memories if m["id"]==proposed.get("id")),None) if action!="add" else None
        if action!="add" and (old is None or old["id"] in touched or old["id"] not in {m["id"] for m in related}): raise ValueError("target_missing_or_repeated")
        old_fact=decode_fact(old) if old else None
        if old and not old_fact: raise ValueError("legacy_record_requires_manual_review")
        op={"action":action,"transition":proposed["relation"]}
        if proposed["relation"] in {"completed","cancelled","corrected","invalidated"} and source["intent"]!=proposed["relation"]:
            raise ValueError("transition_not_from_user_intent")
        if proposed["relation"]=="changed" and source["intent"] not in {"change","relative"}:
            raise ValueError("change_not_explicit")
        if old: op.update(id=old["id"],expectedVersion=old.get("version",0))
        if action=="delete":
            if source["intent"]!="invalidated": raise ValueError("deletion_without_explicit_intent")
            if fact_key(source["fact"])!=fact_key(old_fact): raise ValueError("source_identity_not_canonical")
            return op,old
        if action=="add":
            if source["intent"] in {"relative","completed","cancelled","invalidated"}: raise ValueError("relative_change_without_current_target")
            fact=source["fact"].copy()
        else:
            raw=proposed.get("fact")
            if not isinstance(raw,dict): raise ValueError("fact_not_object")
            # 普通更新的ID由已校验对象目录赋予，不接受模型换ID避开门禁。
            metadata={k:source["fact"][k] for k in ("entityId","predicateId","canonicalPredicate","predicateDefinition","entityAliases","predicateAliases")}
            metadata["subject"]=source["fact"]["subject"]
            for key in ("contextEvidence","contextMessageId"):
                if key in source["fact"]: metadata[key]=source["fact"][key]
            for key in ("memoryMode","memoryField"):
                if key in source["fact"]: metadata[key]=source["fact"][key]
            if proposed["relation"]=="completed":
                code=("completed_"+source["fact"]["canonicalPredicate"])[:48]
                metadata.update(predicateId="p_"+digest(normalize(code))[:40],canonicalPredicate=code,
                    predicateDefinition=("已完成的"+source["fact"]["predicateDefinition"])[:160],predicateAliases=[raw["predicate"]])
            else: metadata["predicate"]=source["fact"]["predicate"]
            fact=validate_fact({**raw,**metadata},base["latestUser"],observed)
            if proposed["relation"] != "completed" and source["intent"] != "relative":
                if not same_value(fact["value"],source["fact"]["value"]):
                    raise ValueError("update_value_not_from_source")
            if fact["sourceEvidence"] != source["fact"]["sourceEvidence"]:
                raise ValueError("operation_evidence_changed")
        category=self._category(proposed.get("category",source["category"]),fact)
        if category not in CATEGORIES: raise ValueError("category_invalid")
        terminal=old_fact is not None and proposed["relation"] in {"completed","cancelled"}
        if terminal and (source["intent"]!=proposed["relation"] or old["category"]!="goal" or old_fact["temporalScope"]!="current" or fact_key(source["fact"])!=fact_key(old_fact)):
            raise ValueError("terminal_without_matching_active_goal")
        if terminal and fact.get("memoryMode")=="automatic": fact["memoryField"]=category
        if fact.get("memoryMode")=="automatic" and category!=fact.get("memoryField"):
            raise ValueError("automatic_category_changed")
        if old_fact and proposed["relation"]=="completed":
            if old["category"]!="goal" or fact["temporalScope"]!="historical" or category!="event" or entity_id(source["fact"])!=entity_id(old_fact): raise ValueError("completion_without_matching_goal")
        elif old_fact and proposed["relation"]=="cancelled":
            if category!="goal" or fact["temporalScope"]!="historical" or fact_key(fact)!=fact_key(old_fact): raise ValueError("cancellation_without_matching_goal")
        elif old_fact:
            if fact_key(fact)!=fact_key(old_fact) or fact_key(source["fact"])!=fact_key(old_fact): raise ValueError("source_identity_not_canonical")
            if source["fact"]["temporalScope"]!=fact["temporalScope"] or source["fact"]["temporalScope"]!="current" and (source["fact"]["timePrecision"],source["fact"]["timeValue"])!=(fact["timePrecision"],fact["timeValue"]): raise ValueError("target_time_slot_changed")
            if not same_slot(old_fact,fact): raise ValueError("target_time_slot_changed")
        if old_fact and not terminal: check_numeric_transition(source,old_fact,fact)
        if action=="add":
            self._check_numeric_binding(fact,memories)
            duplicates=[m for m in memories if (f:=decode_fact(m)) and same_slot(f,fact)]
            if duplicates:
                if any(same_value(decode_fact(m)["value"],fact["value"]) for m in duplicates): return None,None
                raise ValueError("existing_slot_requires_update")
            for pending in added:
                if same_slot(pending,fact):
                    if same_value(pending["value"],fact["value"]): return None,None
                    raise ValueError("conflicting_add_in_turn")
        if old_fact and not terminal and same_value(old_fact["value"],fact["value"]) and old_fact["observedAt"]==fact["observedAt"] and old["category"]==category: return None,old
        op.update(fact=fact,category=category)
        return op,old

    @staticmethod
    def _check_numeric_binding(fact,memories):
        """只发现潜在对象混写，不猜对象/增减语义，不批准任何更新。

        比如新值“1台打印机”的数值尾部“台打印机”，与已有“打印机数量=2台”
        存在结构关联。不能在另一个事实槽直接当成独立绝对状态保存。
        单位及对象来自已有事实，不维护宠物/车辆等业务词表。
        """
        number=numeric_quantity(fact["value"])
        if number is None: return
        for memory in memories:
            old=decode_fact(memory)
            if not old or fact_key(old)==fact_key(fact): continue
            previous=numeric_quantity(old["value"])
            if previous is None or not previous[1] or not number[1].startswith(previous[1]): continue
            suffix=normalize(number[1][len(previous[1]):])
            names=[old["subject"],*old.get("entityAliases",[])]
            if suffix and any(suffix in normalize(name) for name in names):
                raise ValueError("numeric_entity_binding_unresolved")

    async def _bind_relative_target(self, source, base, registry, memories, observed):
        """为粗粒度数量断言选目标；值/增量不可由关联模型重新生成。"""
        if source["intent"]!="relative" or source["fact"]["temporalScope"]!="current": return None
        if any((old:=decode_fact(m)) and same_slot(old,source["fact"]) for m in memories): return None
        # 已规范化别名的快路径无需再次请求语义关联。
        direct=self._direct_resolution(source,registry,base)
        if direct and direct.get("predicateId") is not None: return None
        candidates=[]; used=0
        for m in memories:
            old=decode_fact(m)
            if not old or old.get("temporalScope")!="current": continue
            number=numeric_quantity(old["value"])
            if number is None: continue
            item={"id":m["id"],"subject":old["subject"],"predicate":old["predicate"],
                  "definition":registry.predicates[predicate_id(old)]["definition"],
                  "value":old["value"],"unit":number[1]}
            size=len(json.dumps(item,ensure_ascii=False))
            if len(candidates)>=20 or used+size>3500: continue
            candidates.append(item); used+=size
        if not candidates: return None
        binding=await self._ask_json(QUANTITY_BIND_PROMPT,{**base,"sourceFact":source["originalFact"],
            "intent":source["intent"],"delta":source["delta"],"targets":candidates},tokens=350)
        target=binding.get("targetId")
        if not isinstance(target,int) or isinstance(target,bool) or binding.get("sameQuantity") is not True:
            raise ValueError("relative_quantity_target_unresolved")
        chosen=next((c for c in candidates if c["id"]==target),None)
        if chosen is None or binding.get("unit")!=chosen["unit"]:
            raise ValueError("relative_quantity_target_invalid")
        from decimal import Decimal
        tails=[]
        for match in re.finditer(r"([+-]?\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]+)(\s*[^\d\s，,。；;！？!?\n]{0,12})",
                                 source["fact"]["sourceEvidence"]):
            number=numeric_quantity(match.group(1))
            if number and abs(number[0])==abs(Decimal(str(source["delta"]))) and match.group(2).strip():
                tails.append(match.group(2).strip())
        # 原话明确出现的单位也必须相符：不能因模型说“同一数量”，
        # 把“增加一台”连接到年龄（岁）。无明确单位不在此臆测。
        if chosen["unit"] and tails and not any(tail.startswith(chosen["unit"]) for tail in tails):
            raise ValueError("relative_quantity_unit_not_supported")
        old=decode_fact(next(m for m in memories if m["id"]==target))
        entity=registry.entities[entity_id(old)]; predicate=registry.predicates[predicate_id(old)]
        f={**source["fact"],"subject":old["subject"],"predicate":old["predicate"],
           "entityId":entity["id"],"predicateId":predicate["id"],
           "canonicalPredicate":predicate["code"],"predicateDefinition":predicate["definition"],
           "entityAliases":entity["aliases"],"predicateAliases":predicate["aliases"]}
        return {**source,"fact":validate_fact(f,base["latestUser"],observed),
                "resolution":{"method":"relative_quantity_binding","targetId":target,
                              "originalSubject":source["originalFact"]["subject"],
                              "originalPredicate":source["originalFact"]["predicate"]}}

    async def _process_source(self, source, base, registry, memories, touched, added, observed):
        if source["category"]=="note" and (source["fact"].get("memoryMode")!="explicit" or not source.get("noteReason")):
            self._diagnose("memory_policy","note_requires_explicit_fallback"); return None
        canonical=await self._bind_relative_target(source,base,registry,memories,observed)
        if canonical is None: canonical=await self._canonicalize(source,base,registry,observed)
        related=[]; used=0
        for memory in sorted(memories,key=lambda m:not ((f:=decode_fact(m)) and fact_key(f)==fact_key(canonical["fact"])) )[:20]:
            item=prompt_memory(memory); size=len(json.dumps(item,ensure_ascii=False))
            if used+size>4000: break
            related.append(item); used+=size
        data={**base,"latestUser":canonical["fact"]["sourceEvidence"],"sourceFact":canonical,"relatedMemories":related}
        proposed=await self._plan_operation(canonical,data,memories)
        try: op,old=self._validate_operation(canonical,proposed,memories,related,touched,added,base,observed)
        except ValueError as error:
            self._diagnose("hard_check",error,canonical["fact"])
            if str(error) not in REPAIRABLE or canonical["repaired"]: return None
            repaired=await self._ask_json(REPAIR_PROMPT,{**base,"error":str(error),"sourceFact":canonical,"proposedOperation":proposed,"relatedMemories":related})
            if repaired.get("correctedSource") is not None:
                corrected=self._repaired_source(repaired["correctedSource"],canonical["originalFact"],base,observed)
                if corrected["category"]!=canonical["category"]: raise ValueError("repair_cannot_change_category")
                for key in ("memoryMode","memoryField"):
                    if key in canonical["fact"]: corrected["fact"][key]=canonical["fact"][key]
                for key in ("automaticCategoryDefinition","noteReason"):
                    if key in canonical: corrected[key]=canonical[key]
                canonical=await self._canonicalize(corrected,base,registry,observed)
            canonical["repaired"]=True
            proposed=repaired.get("operation")
            if not isinstance(proposed,dict): raise ValueError("repair_operation_invalid")
            if proposed.get("action")=="none": return None
            op,old=self._validate_operation(canonical,proposed,memories,related,touched,added,base,observed)
        if op is None: return None
        # 这些只描述程序已检查的范围，不声称程序证明了语义或数据库版本仍未变化。
        hard={"sourceQuote":True,"calendarBounds":True,"catalogIds":True,
              "numericConsistency":"checked_when_numeric","targetVersion":"from_snapshot" if old else "not_applicable",
              "slotDeduplication":True}
        self._diagnose("hard_check","passed",canonical["fact"])
        focused_source={k:canonical[k] for k in ("fact","category","intent","delta","automaticCategoryDefinition","noteReason") if k in canonical}
        audit_context=base.get("userContext",[]) if canonical["fact"].get("contextEvidence") else []
        target=prompt_memory(old) if old else None
        self._diagnose("audit_target",json.dumps({"action":op["action"],"transition":op.get("transition"),
            "targetId":old.get("id") if old else None,
            "oldFact":{k:redact(str(decode_fact(old).get(k,"")))[:80] for k in ("subject","predicate","value")} if old else None},ensure_ascii=False),canonical["fact"])
        review=await self._ask_json(self.audit_prompt,{"latestUser":base["latestUser"],"referenceTime":base.get("referenceTime"),
            "userContext":audit_context,"sourceFact":focused_source,"proposedOperation":op,
            "targetMemory":target,"hardChecks":hard},tokens=700)
        if not audit_approved(review) or (source["category"]=="note" and review.get("noteFallback") is not True):
            self._diagnose("semantic_audit",[k for k in CHECKS if review.get(k) is not True],canonical["fact"])
            if isinstance(review,dict) and isinstance(review.get("reason"),str):
                self._diagnose("semantic_audit_detail",redact(review["reason"])[:120],canonical["fact"])
            return None  # 明确语义否决不重试；不能反复改到模型批准。
        return op

    async def _plan_operation(self, source, data, memories):
        """事实ID已确定后，常规新增/重复/数字更新不再让模型自由选择目标或计算结果。"""
        f=source["fact"]
        slots=[m for m in memories if (old:=decode_fact(m)) and same_slot(old,f)]
        selected=next((m for m in memories if m["id"]==source.get("selectedTargetId")),None)
        if source.get("controlledField") and source["intent"]=="invalidated" and (selected or len(slots)==1):
            target=selected or slots[0]
            return {"action":"delete","id":target["id"],"relation":source["intent"]}
        if source["intent"] in {"completed","cancelled"}:
            target=selected or (slots[0] if len(slots)==1 else None)
            if not target or target.get("category")!="goal": raise ValueError("terminal_goal_target_unresolved")
            f={**f,"temporalScope":"historical","timePrecision":"day","timeValue":f["observedAt"]}
            return {"action":"update","id":target["id"],"relation":source["intent"],"fact":f,
                    "category":"event" if source["intent"]=="completed" else "goal"}
        if source["intent"] in {"assert","change","corrected"} and not slots:
            return {"action":"add","relation":"new","fact":f,"category":source["category"],"method":"new_slot"}
        if len(slots)==1:
            target=slots[0]; old=decode_fact(target)
            if source["intent"] in {"assert","change","corrected"} and same_value(old["value"],f["value"]):
                return {"action":"none","method":"same_value"}
            before=numeric_quantity(old["value"])
            stated=numeric_quantity(f["value"])
            if source["intent"]=="relative" and before is not None:
                from decimal import Decimal
                final=before[0]+Decimal(str(source["delta"]))
                value=format(final,"f")
                if "." in value: value=value.rstrip("0").rstrip(".")
                f={**f,"value":value+before[1]}
            elif source["intent"] in {"assert","change","corrected"} and before is not None and stated is not None and before[1]==stated[1]:
                pass
            elif source["intent"] in {"assert","change","corrected"} and before is None and stated is None:
                # 同一属性/时间槽的普通状态改变也由程序选择唯一目标，
                # 不需要模型再生成一套对象、属性、值和操作。
                return {"action":"update","id":target["id"],
                    "relation":"corrected" if source["intent"]=="corrected" else "changed" if source["intent"]=="change" else "refines",
                    "fact":f,"category":source["category"],"method":"asserted_transition"}
            else:
                return await self._ask_json(self.align_prompt,data)
            return {"action":"update","id":target["id"],"relation":"corrected" if source["intent"]=="corrected" else "changed" if source["intent"] in {"change","relative"} else "refines",
                    "fact":f,"category":source["category"],"method":"numeric_transition"}
        return await self._ask_json(self.align_prompt,data)


def redact_tree_safe(value):
    from .facts import redact_tree
    return redact_tree(value)
