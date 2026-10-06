"""手动真实模型验收；仅使用合成样例，HTTP 读写硬隔离。

在服务目录运行 python tests/check_memory_live.py [样例名...]。
不参与自动测试发现，避免意外消耗模型额度。
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.config_loader import load_config
from core.utils.llm import create_instance
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
from core.providers.memory.mem_local_short.facts import validate_fact, render_fact, fact_key, numeric_quantity, same_slot

WHEN = datetime(2026, 9, 13, 10, 0)
MODULE = "core.providers.memory.mem_local_short.mem_local_short"


def old(predicate, value, evidence, id=8, subject="user", scope="current", time="2025", category="profile", precision="year"):
    f = validate_fact(dict(subject=subject, predicate=predicate, value=value, evidence=evidence,
                           temporalScope=scope, timePrecision=precision, timeValue=time), evidence, WHEN.date())
    return dict(id=id, version=2, category=category, factJson=json.dumps(f, ensure_ascii=False),
                factKey=fact_key(f), content=render_fact(f))


def valid(label, ops):
    if label == "transient": return ops == []
    if not ops: return False
    # 同槽不同新增会被真实数据库拒绝，不能因包含期望文字就算成功。
    additions=[op["fact"] for op in ops if op["action"]=="add"]
    if any(same_slot(a,b) for i,a in enumerate(additions) for b in additions[i+1:]): return False
    values = str(ops)
    if label == "profile": return len(ops)>=2 and any(op["fact"]["subject"]=="user" and op["fact"]["value"] in ("21", "21岁") for op in ops) and any(op["fact"]["subject"]=="user" and "大学" in op["fact"]["value"] for op in ops) and all(op["action"] == "add" for op in ops)
    if label == "grade": return len(ops)>=2 and any(op["fact"]["subject"]=="user" and op["fact"]["value"] in ("21", "21岁") for op in ops) and any(op["fact"]["subject"]=="user" and ("三" in op["fact"]["value"] or "3" in op["fact"]["value"]) for op in ops)
    if label == "quantity":
        return len(ops)==1 and ops[0]["action"]=="update" and ops[0].get("id")==8 and ops[0]["fact"]["subject"]=="用户的猫" and ops[0]["fact"]["temporalScope"]=="current" and numeric_quantity(ops[0]["fact"]["value"])==numeric_quantity("3只")
    if label == "completed": return len(ops)==1 and ops[0]["action"] == "update" and ops[0].get("id") == 9 and ops[0]["fact"]["temporalScope"] == "historical" and ops[0]["category"] == "event" and any(place in ops[0]["fact"]["value"] for place in ("日本", "Japan"))
    if label == "history": return all(op["action"] == "add" and op["fact"]["temporalScope"] == "historical" and op["fact"]["timeValue"] == "2025" for op in ops) and any("猫" in str((op["fact"]["subject"],op["fact"]["predicate"],op["fact"]["value"])) and any(v in op["fact"]["value"] for v in ("两", "2")) for op in ops)
    if label == "market": return any(op["action"] == "update" and op["id"] == 10 and "市场" in op["fact"]["value"] and op["fact"]["timeValue"] == "2025" for op in ops) and not any(op.get("id") == 8 for op in ops)
    if label == "mixed_time": return any(op["fact"]["value"] == "上海" and op["fact"]["temporalScope"] == "historical" for op in ops) and any(op["fact"]["value"] == "北京" and op["fact"]["temporalScope"] == "current" for op in ops)
    return "明天会更好" in values and all(op["fact"]["temporalScope"] == "current" for op in ops)


async def main():
    config = load_config()
    selected = config["Memory"][config["selected_module"]["Memory"]]
    name = selected.get("llm") or config["selected_module"]["LLM"]
    settings = {**config["LLM"][name], "timeout": 30}
    llm = create_instance(settings.get("type", name), settings)
    if hasattr(llm, "client") and hasattr(llm.client, "with_options"):
        llm.client = llm.client.with_options(max_retries=0)
    print("LIVE synthetic-only model:", getattr(llm, "model_name", name), flush=True)
    cats = old("数量", "两只", "我有两只猫", subject="用户的猫", category="relationship")
    cases = [
        ("profile", "是啊我现在是大学生21岁", [cats], []),
        ("grade", "我今年21岁大三了", [cats], []),
        ("transient", "我想出门", [], []),
        ("quantity", "我加养了一只猫", [cats], []),
        ("completed", "我已经去过日本回来了", [old("旅行目标", "去日本旅行", "我计划去日本旅行", id=9, scope="future", time="未定日期", precision="approximate", category="goal")], []),
        ("history", "哈喽我去年大二养了两只猫", [cats], []),
        ("market", "对啊我去年大二的时候在市场买的", [old("年龄", "两岁", "猫现在两岁", subject="用户的猫"), old("购买来源", "大二期间购买", "去年大二买了猫", id=10, subject="用户的猫", scope="historical", category="event")], ["我养的两只猫现在两岁"]),
        ("mixed_time", "我去年住在上海，现在住在北京", [], []),
        ("title", "我喜欢歌曲《明天会更好》", [], []),
    ]
    if len(sys.argv) > 1: cases = [case for case in cases if case[0] in sys.argv[1:]]
    if not cases: raise SystemExit("Unknown case")
    failures = []
    original_response=llm.response_no_stream
    def response_with_diagnostics(*args,**kwargs):
        value=original_response(*args,**kwargs)
        llm.last_memory_reply=value
        return value
    llm.response_no_stream=response_with_diagnostics
    if hasattr(llm,"response_json"):
        original_json=llm.response_json
        def json_with_diagnostics(*args,**kwargs):
            value=original_json(*args,**kwargs); llm.last_memory_reply=value; return value
        llm.response_json=json_with_diagnostics
    try:
        for label, text, memories, context in cases:
            # 旧开放协议专项验收；默认产品策略另见 check_memory_policy_live.py。
            provider = MemoryProvider({"memory_policy":"legacy"}); provider.init_memory("synthetic-test", llm)
            payloads = []
            diagnostics = []
            original = provider._ask_json

            async def capture(*args, **kwargs):
                try:
                    result = await original(*args, **kwargs); payloads.append(result); return result
                except Exception as error:
                    diagnostics.append(("model_call",type(error).__name__))
                    if isinstance(error,json.JSONDecodeError):
                        diagnostics.append(("synthetic_bad_json",getattr(llm,"last_memory_reply","")[:1200]))
                    raise

            provider._ask_json = capture
            provider._diagnose = lambda phase,reason,fact=None: diagnostics.append((phase,str(reason)))
            writer = AsyncMock(return_value={"created": 0, "updated": 0, "deleted": 0})
            snapshot = dict(schemaVersion=2, roleId="synthetic-test", userId=7, revision=5, memories=memories)
            with patch(MODULE + ".search_memory_facts", AsyncMock(return_value=snapshot)), patch(MODULE + ".commit_memory_facts", writer), patch(MODULE + ".logger", Mock()):
                result = await provider.save_memory([NS(role="user", content=text, created_at=WHEN, uniq_id=label)], "synthetic-session", user_context=[NS(role="user", content=c, created_at=WHEN) for c in context])
            passed = valid(label, result)
            print(label, "PASS" if passed else "FAIL", result, flush=True)
            if not passed:
                print("synthetic model payloads:", payloads, flush=True)
                print("diagnostics:",diagnostics,flush=True); failures.append(label)
    finally:
        if hasattr(llm, "client"): llm.client.close()
    if failures: raise SystemExit("Live validation failed: " + ", ".join(failures))


if __name__ == "__main__": asyncio.run(main())
