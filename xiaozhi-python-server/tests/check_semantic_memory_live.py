"""手动真实模型基准，Java/数据库完全替换为内存夹具。

在服务目录：python tests/check_semantic_memory_live.py --live --output ../output/semantic-memory-live.json
会消耗配置模型的额度；不会启动服务、写真实记忆。报告自动判定只是筛查，需检查具体记录。
"""
import argparse
import asyncio
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config.config_loader import load_config
from core.utils.llm import create_instance
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
from core.providers.memory.mem_local_short import semantic
from semantic_memory_fixture import MemoryFixture


def evaluate(case, rows, before, ops, recall):
    errors=[]
    active=[r for r in rows if r.get("status")!="invalidated"]
    expected=case.get("expected")
    if expected is not None:
        if len(active)!=len(expected): errors.append("record_count")
        matched=set()
        for want in expected:
            found=next((r for r in active if r["id"] not in matched and r["category"]==want["category"]
                and r.get("status")==want.get("status","active")
                and all(re.search(p,r["content"]) for p in want.get("contains",[]))
                and not any(re.search(p,r["content"]) for p in want.get("excludes",[]))),None)
            if found: matched.add(found["id"])
            else: errors.append("missing_expected:"+str(want))
    for id in case.get("preserveIds",[]):
        if next((r for r in rows if r["id"]==id),None)!=next((r for r in before if r["id"]==id),None): errors.append("wrong_update:"+str(id))
    if "lastReasons" in case and sorted(o["reason"] for o in ops)!=sorted(case["lastReasons"]): errors.append("operation_reasons")
    if "lastTargetIds" in case and sorted(o.get("targetId", -1) for o in ops)!=sorted(case["lastTargetIds"]): errors.append("operation_targets")
    for pattern in case.get("recallContains",[]):
        if not re.search(pattern,recall): errors.append("recall_missing:"+pattern)
    for pattern in case.get("recallExcludes",[]):
        if re.search(pattern,recall): errors.append("recall_wrong:"+pattern)
    return errors


async def main(args):
    benchmark=json.loads(Path(__file__).with_name("semantic_memory_benchmark.json").read_text(encoding="utf-8"))
    config=load_config()
    selected=config["Memory"][config["selected_module"]["Memory"]]
    name=selected.get("llm") or config["selected_module"]["LLM"]
    settings={**config["LLM"][name],"timeout":30}
    llm=create_instance(settings.get("type",name),settings)
    results=[]
    try:
        for case in benchmark["cases"]:
            if args.case and case["id"] not in args.case: continue
            provider=MemoryProvider({}); provider.init_memory("synthetic-semantic",llm)
            db=MemoryFixture(case.get("initial",[])); before=copy.deepcopy(db.rows)
            diagnostics=[]
            provider._diagnose=lambda phase,reason,fact=None: diagnostics.append({"phase":phase,"reason":str(reason),"candidate":fact})
            operations=[]; messages=[]; last=[]; recall=""
            with patch.object(semantic,"semantic_memory_snapshot",db.snapshot),patch.object(semantic,"semantic_memory_request",db.request),patch.object(semantic,"semantic_memory_history",db.history):
                for index,text in enumerate(case["turns"]):
                    msg=SimpleNamespace(role="user",content=text,created_at=datetime.fromisoformat(benchmark["referenceTime"]),uniq_id=f"{case['id']}-{index}")
                    last=await provider.save_memory([msg],"synthetic",messages)
                    operations.append(last); messages.append(msg)
                if case.get("query"): recall=await provider.query_memory(case["query"])
            errors=evaluate(case,db.rows,before,last or [],recall)
            if any(op is None for op in operations): errors.append("pipeline_failure")
            results.append({"id":case["id"],"screeningPass":not errors,"errors":errors,"operations":operations,
                            "finalMemories":db.rows,"history":db.history_rows,"recall":recall,"diagnostics":diagnostics,"manualReview":case.get("review","")})
            print(case["id"],"PASS" if not errors else "FAIL",flush=True)
    finally:
        if hasattr(llm,"client"): llm.client.close()
    report={"benchmarkVersion":benchmark["version"],"model":getattr(llm,"model_name",name),"executedAt":datetime.now().astimezone().isoformat(),
            "storage":"synthetic fixture; does not verify Java transactions", "requiresSemanticReview":True,"cases":results}
    path=Path(args.output); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"自动筛查通过 {sum(r['screeningPass'] for r in results)}/{len(results)}；报告：{path}")
    return int(any(not r["screeningPass"] for r in results))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true",help="明确启用真实模型调用")
    parser.add_argument("--case",action="append",help="仅运行指定案例，可重复")
    parser.add_argument("--output",default="../output/semantic-memory-live.json")
    args=parser.parse_args()
    if not args.live: parser.error("真实模型调用需要显式 --live")
    raise SystemExit(asyncio.run(main(args)))
