"""轻量语义记忆：提取、程序粗召回、单次操作决策、确定性执行。"""
import json
import re
from decimal import Decimal

from config.manage_api_client import semantic_memory_snapshot, semantic_memory_request, semantic_memory_history
from .facts import redact, redact_tree, normalize, numeric_mentions
from .categories import AUTO_CATEGORIES
from .policy import refusal_request, authorized_segments

EXTRACT = """仅根据latestUser提取本轮长期事实或对旧记忆的修改意图，输出JSON {"memories": [...]}，最多8条。
每条为category/key/content/source/intent_hint/needsContext。类别来自categories；key是具体主题，不是身份ID。
content为完整自然句，保留主语、动作、数量、否定和计划/完成/取消含义。source可省略，程序使用完整latestUser。
若填写source必须连续引用latestUser，不为拆分事实改写原话；猫狗两条可共用整句来源。
intent_hint可为assert/changed/corrected/relative/completed/cancelled/invalidated/uncertain，只是给决策阶段的提示。
修改旧记忆不要求有新增价值：不再、取消、放弃、完成、说错等仍提取，不能因否定而丢掉。
不计划去美国是取消目标；六级过了是完成目标；从未养狗可作废旧饲养记录；不要只输出美国或六级这样的名词。
猫和狗数量分别提取；同句取消美国计划和新增日本计划分别提取。不要带入旧对话事实。
省略对象如“我说错了，其实只有两只”保留原话，needsContext=true，交给决策结合旧记录判断。
needsContext必须是布尔值；普通“我”不算指代不清。relative只描述变化，并可给delta，不猜总量。
changed表示过去是真的后来变化；corrected表示旧值本来错误；invalidated表示旧事实从未成立。
note仅允许用户主动要求且其他类别不适用，source必须是authorizedSegments中的授权分句。
密码、验证码、密钥不记。假设、引用、提问不能当真实状态。没有任何候选返回空列表。
"""

DECIDE = """结合latestUser、candidates和relatedMemories，一次决定本轮全部最终记忆操作。只输出JSON {"operations": [...]}。
程序仅粗召回，顺序、key相等和category相等都不代表已绑定。你负责同义、上下位关系、所有者、指代、主题和状态语义。
没有可靠旧目标而有明确新事实，应ADD，不因没有target就UNCERTAIN。修改目标不明确时UNCERTAIN，不能猜ID。
只可修改relatedMemories提供的id；UPDATE等必须返回它的expectedVersion和sameTarget=true。
同句可以取消旧目标并新增新目标；一个目标一轮只修改一次。SKIP表示重复或不值得记；UNCERTAIN不执行。
候选仅为提取提示，intent_hint不是命令；以完整latestUser确定纠错作用域，不能让“说错了”影响无关新增事实。
changed是现实变化，corrected是纠正旧值，旧content与新content不同是正常现象。key可换成更合适的同主题名称。
取消美国计划可关联美国洛杉矶目标；不要把日本、朋友或其他宠物绑定成同一个目标。多个可能对象且原话不足以区分时UNCERTAIN。
输出动作（action大写）：
ADD: reason=new，memory={category,key,content,source}。
UPDATE: reason=changed/corrected/refines，targetId/expectedVersion/sameTarget，memory为本轮新事实。
CANCEL: reason=cancelled，目标必须active goal；memory.category=goal，写出取消后的陈述。
COMPLETE: reason=completed，目标必须active goal；memory.category=goal（保留已完成目标），另外必须给event={category:event,key,content,source}，生成独立完成事实。
INVALIDATE: reason=invalidated，明确旧记录从未成立或要求作废，targetId/expectedVersion/sameTarget/source；不是普通现实变化。
SKIP或UNCERTAIN: explanation说明原因。
写操作需candidateIndex指向对应candidates下标；提取漏掉了原话明确的修改时可为null，此时必须提供完整证据并接受额外审查。
每项还需needsAudit布尔值；指代、归属歧义、复杂纠错应true。不要为普通绝对值更新重复生成旧值。
绝对值更新优先逐字使用candidate.content；改写必须忠实原话，不把target.content覆盖到新memory。
相对数量UPDATE额外给quantity={before:"三只",after:"四只",delta:1}，before/after分别连续出现在旧/新content，程序核算。
非相对变化不能用quantity。source可省略由程序使用完整latestUser；若填写必须连续引用，不能改写。时间采用referenceTime，不编造日期。
类别合法、完整自然句、原子事实。不要从relatedMemories生成本轮未说的新事实。note只在本轮明确授权时允许。
即使candidates为空也检查latestUser是否明确修改已有记忆；假设、引用和提问不写入。
最多8项最终数据库写入，COMPLETE占两项。不要要求旧新时间戳、content或status相等。
"""

RISK_AUDIT = """仅审查高风险操作，不重新提取、不重新评价记忆价值、不比较字段是否相等。
本次plan是唯一决策结果。核对latestUser是否支持新断言、是否改错所有者/对象/主题、指代是否足够明确。
oldTargets只作为被修改对象的背景，不是新值；新值只读plan中的memory或event。数量和否定变化是正常更新。
相对数量算术由程序检查，这里只核对增减方向、单位和归属。COMPLETE是关闭旧goal并增加完成事件。
INVALIDATE仅检查用户是否否认旧事实成立/明确作废，不要求用户重新肯定旧事实。
禁止因content、key措辞、observedAt、status不同而拒绝。仅输出 {"errors":[]}，明确风险用errors字符串列表引用冲突证据。
"""

def clean_text(value, cap):
    if not isinstance(value, str) or not normalize(value) or len(value) > cap:
        raise ValueError("invalid_text")
    if redact(value) != value or "[REDACTED]" in value:
        raise ValueError("sensitive_content")
    return value.strip()


def check_quantity(target, proposed, quantity, delta):
    """仅相对数量需要临时算术证据，不为每条记忆添加永久数值字段。"""
    from .facts import numeric_quantity
    if not isinstance(quantity, dict) or isinstance(delta, bool) or not isinstance(delta, (int, float)):
        raise ValueError("quantity_unresolved")
    before, after = quantity.get("before"), quantity.get("after")
    if not isinstance(before, str) or not isinstance(after, str) or before not in target or after not in proposed:
        raise ValueError("quantity_evidence_invalid")
    a, b = numeric_quantity(before), numeric_quantity(after)
    amount = Decimal(str(delta))
    if not a or not b or not amount.is_finite() or a[1] != b[1] or a[0] + amount != b[0] or b[0] < 0:
        raise ValueError("quantity_arithmetic_invalid")


def batches(records, cap=6500):
    batch, used = [], 0
    for record in records:
        # 完整content参与判断；不截断成可能误导目标匹配的片段。
        item = {k: record[k] for k in ("id", "category", "key", "content", "status", "time") if k in record}
        size = len(json.dumps(item, ensure_ascii=False))
        if size > cap:
            raise ValueError("record_budget_exceeded")
        if used + size > cap:
            yield batch
            batch, used = [], 0
        batch.append(item)
        used += size
    if batch:
        yield batch


def prompt_memory(memory):
    """同一原话只在base提供一次，避免每个对象重复附带source挤爆上下文。"""
    return {k: memory[k] for k in ("id", "category", "key", "content", "status", "time", "observedAt") if k in memory}


def parse_candidate(raw, text, observed):
    if not isinstance(raw, dict):
        raise ValueError("candidate_not_object")
    memory, explicit = parse_memory(raw, text, observed)
    hint = raw.get("intent_hint", "assert")
    if hint not in {"assert", "changed", "corrected", "relative", "completed", "cancelled", "invalidated", "uncertain"}:
        raise ValueError("intent_hint_invalid")
    if type(raw.get("needsContext", False)) is not bool:
        raise ValueError("needs_context_invalid")
    return {"memory": memory, "intent_hint": hint, "needsContext": raw.get("needsContext", False), "explicit": explicit}


def source_reference(raw, text, require_explicit=False):
    source = raw.get("source")
    if source is None or isinstance(source, str) and not source.strip():
        if require_explicit:
            raise ValueError("note_requires_explicit_source")
        # 证据来自调用方捕获的真实用户消息，不能由模型补写或从历史继承。
        return text
    if not isinstance(source, str):
        raise ValueError("source_reference_invalid_type")
    source = source.strip()
    if source not in text:
        raise ValueError("source_reference_not_substring")
    return source


def parse_memory(raw, text, observed):
    if not isinstance(raw, dict) or raw.get("category") not in {*AUTO_CATEGORIES, "note"}:
        raise ValueError("category_invalid")
    source = source_reference(raw, text, require_explicit=raw["category"] == "note")
    memory = {"category": raw["category"], "key": clean_text(raw.get("key"), 120),
              "content": clean_text(raw.get("content"), 2000), "source": source, "observedAt": observed}
    if "time" in raw:
        memory["time"] = clean_text(raw["time"], 120)
    explicit = any(source.strip() == segment for segment in authorized_segments(text))
    if memory["category"] == "note" and not explicit:
        raise ValueError("note_requires_explicit_source")
    return memory, explicit


def terms(text):
    # 通用字/词重叠用于排序，不引入地名、宠物或状态关键词规则。
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalize(text))
    return set(tokens) | {a+b for a, b in zip(tokens, tokens[1:])}


def retrieve_related(records, candidates, text, cap=4800, limit=20):
    query = terms(text + " " + " ".join(c["memory"]["key"] + " " + c["memory"]["content"] for c in candidates))
    categories = {c["memory"]["category"] for c in candidates}
    eligible = [r for r in records if r.get("status") != "invalidated"]
    def score(row):
        words = terms(row["key"] + " " + row["content"])
        return len(query & words) / max(1, len(words)) + (0.3 if row["category"] in categories else 0)
    ranked = sorted(eligible, key=lambda r: (-score(r), r["id"]))
    selected, used = [], 0
    for row in ranked:
        item = {**prompt_memory(row), "version": row["version"]}
        size = len(json.dumps(item, ensure_ascii=False))
        if len(selected) < limit and used + size <= cap:
            selected.append(item)
            used += size
    return selected, len(eligible) - len(selected)


def validate_plan(answer, candidates, related, records, text, observed):
    """只验证执行边界；目标和原因由同一次LLM决策给出。整批失败不部分提交。"""
    raw_ops = answer.get("operations")
    if not isinstance(raw_ops, list) or len(raw_ops) > 8:
        raise ValueError("decision_envelope_invalid")
    visible = {r["id"]: r for r in related}
    operations, touched, risk_targets = [], set(), []
    needs_audit = False
    for raw in raw_ops:
        if not isinstance(raw, dict) or not isinstance(raw.get("action"), str):
            raise ValueError("operation_invalid")
        action = raw["action"].upper()
        if action == "UNCERTAIN":
            raise ValueError("decision_uncertain")
        if action == "SKIP":
            continue
        if action not in {"ADD", "UPDATE", "CANCEL", "COMPLETE", "INVALIDATE"}:
            raise ValueError("operation_invalid")
        if type(raw.get("needsAudit")) is not bool:
            raise ValueError("audit_flag_invalid")
        index = raw.get("candidateIndex")
        if index is not None and (type(index) is not int or not 0 <= index < len(candidates)):
            raise ValueError("candidate_index_invalid")
        item = candidates[index] if index is not None else None
        needs_audit |= raw["needsAudit"] or item is None or bool(item and item["needsContext"])
        target = None
        reason = raw.get("reason")
        allowed = {"ADD": {"new"}, "UPDATE": {"changed", "corrected", "refines"},
                   "CANCEL": {"cancelled"}, "COMPLETE": {"completed"}, "INVALIDATE": {"invalidated"}}
        if reason not in allowed[action]:
            raise ValueError("action_reason_invalid")
        if action != "ADD":
            target_id = raw.get("targetId")
            if type(target_id) is not int or target_id not in visible:
                raise ValueError("target_not_in_related")
            target = visible[target_id]
            if raw.get("sameTarget") is not True or target_id in touched:
                raise ValueError("target_binding_invalid")
            if type(raw.get("expectedVersion")) is not int or raw["expectedVersion"] != target["version"]:
                raise ValueError("target_version_invalid")
            if target["status"] == "closed" and reason not in {"corrected", "invalidated"}:
                raise ValueError("closed_target_requires_new_occurrence")
            if action in {"CANCEL", "COMPLETE"} and (target["category"] != "goal" or target["status"] != "active"):
                raise ValueError("goal_target_not_active")
            touched.add(target_id)
            risk_targets.append(target)
        elif raw.get("targetId") is not None or raw.get("expectedVersion") is not None:
            raise ValueError("add_cannot_target_existing")
        if action == "INVALIDATE":
            source = source_reference(raw, text)
            # 作废已有note不是新增note，不要求用户再次授权保存它。
            memory = {k: target[k] for k in ("category", "key", "content", "time") if k in target}
            memory.update(source=source, observedAt=observed)
            explicit = False
            needs_audit = True
        else:
            memory, explicit = parse_memory(raw.get("memory"), text, observed)
            if action in {"CANCEL", "COMPLETE"} and memory["category"] != "goal":
                raise ValueError("goal_close_category_invalid")
            # 绝对值提案若改变提取结果，必须额外核对来源，防止二次生成带回旧值。
            needs_audit |= item is None or memory["content"] != item["memory"]["content"]
            needs_audit |= bool(target and target["category"] != memory["category"])
        quantity = raw.get("quantity")
        if quantity is not None:
            if action != "UPDATE" or reason != "changed" or not isinstance(quantity, dict):
                raise ValueError("quantity_operation_invalid")
            delta = quantity.get("delta")
            if type(delta) not in {int, float} or abs(Decimal(str(delta))) not in numeric_mentions(text):
                raise ValueError("quantity_delta_unsupported")
            check_quantity(target["content"], memory["content"], quantity, delta)
            needs_audit = True
        if item and item["intent_hint"] == "relative" and action == "UPDATE" and reason == "changed" and quantity is None:
            raise ValueError("quantity_evidence_missing")
        op = {"action": "add" if action == "ADD" else "delete" if action == "INVALIDATE" else "update",
              "reason": reason, "memory": memory, "explicit": explicit}
        if target:
            op.update(targetId=target["id"], expectedVersion=target["version"])
        operations.append(op)
        if action == "COMPLETE":
            event, event_explicit = parse_memory(raw.get("event"), text, observed)
            if event["category"] != "event":
                raise ValueError("completion_event_required")
            operations.append({"action": "add", "reason": "new", "memory": event, "explicit": event_explicit})
            needs_audit = True
    if len(operations) > 8:
        raise ValueError("operation_budget_exceeded")
    # 数据库前的明显重复检查，key不用于选定target，只作为当前槽冲突提示。
    projected = {r["id"]: dict(r) for r in records}
    for index, op in enumerate(operations):
        old = projected.get(op.get("targetId"))
        if op["action"] == "add" and op["memory"]["category"] != "event" and any(
                r.get("status") == "active" and r["category"] == op["memory"]["category"]
                and normalize(r["content"]) == normalize(op["memory"]["content"])
                and r.get("time") == op["memory"].get("time") for r in projected.values()):
            raise ValueError("duplicate_content")
        status = "invalidated" if op["action"] == "delete" else "closed" if op["reason"] in {"cancelled", "completed"} else old.get("status", "active") if old else "active"
        projected[op.get("targetId", -(index+1))] = {**op["memory"], "status": status}
    slots = set()
    for row in projected.values():
        if row.get("status") != "active" or row["category"] == "event":
            continue
        slot = (row["category"], normalize(row["key"]))
        if slot in slots:
            raise ValueError("current_topic_conflict")
        slots.add(slot)
    # 多条独立写操作属于多对象风险；COMPLETE派生事件与关闭在同一事务提交。
    needs_audit |= len(operations) > 1
    return operations, needs_audit, risk_targets


async def save_turn(provider, text, reference, turn, session, context):
    try:
        if refusal_request(text):
            provider._processed.append(turn)
            return []
        observed = reference.astimezone().isoformat(timespec="seconds")
        extracted = await provider._ask_json(EXTRACT, {"latestUser": text, "referenceTime": observed,
            "trace": f"turn={turn}", "categories": AUTO_CATEGORIES,
            "authorizedSegments": authorized_segments(text)}, tokens=2200, diagnostic="extract")
        values = extracted.get("memories")
        if not isinstance(values, list) or len(values) > 8:
            raise ValueError("extraction_envelope_invalid")
        candidates = []
        for index, raw in enumerate(values):
            try:
                candidates.append(parse_candidate(raw, text, observed))
            except ValueError as exc:
                provider._diagnose("semantic_candidate_invalid", json.dumps(redact_tree({
                    "turn": turn, "candidateIndex": index, "reason": str(exc),
                    "latestUser": text, "candidate": raw}), ensure_ascii=False))
                raise
        provider._diagnose("semantic_extract", f"turn={turn}; candidates={len(candidates)}")
        snapshot = await semantic_memory_snapshot(provider.role_id or "default")
        if snapshot is None:
            raise ValueError("snapshot_unavailable")
        base = {"latestUser": text, "referenceTime": observed, "context": context, "trace": f"turn={turn}"}
        # 原话与陈述时间在base只传一次，避免每条候选重复整句挤占粗召回预算。
        decision_input = {**base, "candidates": [{
            "memory": {k: c["memory"][k] for k in ("category", "key", "content", "time") if k in c["memory"]},
            "intent_hint": c["intent_hint"], "needsContext": c["needsContext"]} for c in candidates],
            "categories": AUTO_CATEGORIES, "authorizedSegments": authorized_segments(text)}
        remaining = 11500 - len(json.dumps(decision_input, ensure_ascii=False))
        if remaining < 0:
            raise ValueError("decision_prompt_budget_exceeded")
        related, omitted = retrieve_related(snapshot["memories"], candidates, text, cap=min(4800, remaining))
        provider._diagnose("semantic_retrieve", f"turn={turn}; ids={[r['id'] for r in related]}; omitted={omitted}")
        answer = await provider._ask_json(DECIDE, {**decision_input,
            "relatedMemories": related, "omittedMemoryCount": omitted}, tokens=3200, diagnostic="decision")
        operations, risk, targets = validate_plan(answer, candidates, related, snapshot["memories"], text, observed)
        if operations and risk:
            audit = await provider._ask_json(RISK_AUDIT, {**base, "plan": answer["operations"],
                "oldTargets": [{k: r[k] for k in ("id", "key", "content")} for r in targets]},
                tokens=900, diagnostic="risk_audit")
            if not isinstance(audit.get("errors"), list) or any(not isinstance(e, str) or not e.strip() for e in audit["errors"]):
                raise ValueError("audit_protocol_invalid")
            if audit["errors"]:
                raise ValueError("audit_error:" + redact(str(audit["errors"]))[:250])
        provider._diagnose("semantic_ready", f"turn={turn}; operations={len(operations)}; audit={'required' if risk else 'skipped'}")
        if not operations:
            provider._processed.append(turn)
            return []
        result = await semantic_memory_request("commit", {"roleId": provider.role_id or "default", "writeProtocolVersion": 4,
            "sessionId": session or "unknown", "turnId": turn, "expectedRevision": snapshot["revision"],
            "source": text, "observedAt": observed, "operations": operations})
        if result is None:
            raise ValueError("commit_unconfirmed")
        provider._feedback_counts = result
        provider._processed.append(turn)
        provider._diagnose("semantic_commit", f"turn={turn}; {result}")
        return operations
    except Exception as exc:
        provider._diagnose("semantic_failure", f"turn={turn}; {type(exc).__name__}: {str(exc)[:250]}")
        return None


RECALL = """按query选择有助于回答的记忆ID，输出 {"ids":[真实id]}，最多12个。
不要编造记录。用户询问以前/曾经的情况时也选择同主题当前记录，以便程序读取真实历史。
closed是已结束目标或经历，不是当前进行中的目标。
每批独立选择，优先直接相关内容。"""


async def recall(provider, query, list_all=False):
    snapshot = await semantic_memory_snapshot(provider.role_id or "default")
    if snapshot is None:
        return "长期记忆数据库本轮读取失败；不要编造记忆。"
    records = [r for r in snapshot["memories"] if r.get("status") != "invalidated"]
    if not list_all:
        selected = set()
        for batch in batches(records):
            answer = await provider._ask_json(RECALL, {"query": query, "records": batch}, tokens=350)
            ids = answer.get("ids")
            if not isinstance(ids, list) or len(ids) > 12 or any(isinstance(i, bool) or not isinstance(i, int) or i not in {r["id"] for r in batch} for i in ids):
                raise ValueError("recall_ids_invalid")
            selected.update(ids)
        records = [r for r in records if r["id"] in selected]
    lines = ["以下是有来源的记忆；日期表示陈述时间，不能自动推算年龄或把已结束目标当当前目标。"]
    used = len(lines[0])
    for record in records:
        line = f"- [{record['status']}; 陈述于{record['observedAt']}] {record['content']}"
        if used + len(line) > 6000:
            lines.append("【仅展示部分记忆，完整记录请查看网页】")
            break
        lines.append(line)
        used += len(line)
        # 普通召回也附相关真实变化；不让模型自行把审计日志解释为事实。
        if not list_all:
            history = await semantic_memory_history(provider.role_id or "default", record["id"])
            if history is None:
                lines.append("  历史读取失败，不能据此断言没有过去状态。")
            for entry in (history or []):
                if entry.get("reason") not in {"changed", "cancelled", "completed"} or not entry.get("oldJson"):
                    continue
                previous = json.loads(entry["oldJson"])
                if previous.get("status") != "active":
                    continue
                line = f"  曾经（陈述于{previous.get('observedAt')}，变更记录于{entry.get('changedAt')}）：{previous.get('content')}"
                if used + len(line) > 6000:
                    break
                lines.append(line)
                used += len(line)
    return "\n".join(lines) if records else "没有检索到相关长期记忆。"
