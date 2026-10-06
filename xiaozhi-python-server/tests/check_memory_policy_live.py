"""受控策略真实模型冒烟验收；合成样例，HTTP 全隔离，不参与自动发现。"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, Mock, patch
from check_memory_live import load_config, create_instance, MemoryProvider, NS, WHEN, MODULE
from core.providers.memory.mem_local_short.facts import digest, validate_fact, numeric_quantity
from core.providers.memory.mem_local_short.policy import core_source


def liked_photography():
    provider=MemoryProvider({})
    source=core_source(provider,dict(field="interest",value="摄影",topic="摄影",intent="assert",evidence="我喜欢摄影"),
                       {"latestUser":"我喜欢摄影"},WHEN.date(),[])
    return dict(id=22,version=2,category="preference",factJson=json.dumps(source["fact"],ensure_ascii=False),content="喜欢摄影")


def opted_count():
    f=validate_fact(dict(subject="用户的猫",predicate="数量",value="两只",evidence="请记住我有两只猫",
        temporalScope="current",entityId="e_cats",predicateId="p_count",canonicalPredicate="cat_count",
        predicateDefinition="用户饲养猫的总数量",entityAliases=["用户的猫"],predicateAliases=["数量"],
        memoryMode="explicit",memoryField="custom"),"请记住我有两只猫",WHEN.date())
    return dict(id=11,version=2,category="relationship",factJson=json.dumps(f,ensure_ascii=False),content="猫数量两只")


def valid(label, ops):
    if ops is None: return False
    fields={op.get("fact",{}).get("memoryField") for op in ops}
    if label in {"transient","purchase","travel","negated","temporary_run","temporary_game","hypothetical_pet","negated_habit","aspirational_habit"}: return ops==[]
    if label=="friend_pet": return not any(f in fields for f in ("pet_name","pet_species","pet_relation","pet_kind"))
    if label=="long_goal": return any(op.get("category")=="goal" and op.get("fact",{}).get("memoryField")=="goal" and "六级" in op["fact"]["value"] and "半年" in op["fact"]["value"] for op in ops)
    if label=="stable_habit": return len(ops)==1 and fields=={"habit"} and "每天晚上" in ops[0]["fact"]["value"] and "跑步" in ops[0]["fact"]["value"]
    if label=="two_named_pets":
        facts=[op.get("fact",{}) for op in ops]
        groups={name:[f for f in facts if name in f.get("entityAliases",[])] for name in ("豆包","雪球")}
        if any({f.get("memoryField") for f in group}!={"pet_name","pet_species","pet_relation"} for group in groups.values()): return False
        ids=[{f.get("entityId") for f in group} for group in groups.values()]
        return all(len(group)==1 for group in ids) and ids[0]!=ids[1] and all(f["value"]=="猫" for f in facts if f.get("memoryField")=="pet_species")
    if label in {"refuse_age","refuse_suffix","repeat_preference"}: return ops==[]
    if label=="negative_preference": return len(ops)==1 and ops[0]["action"]=="update" and ops[0]["id"]==22 and ops[0]["fact"]["value"]=="不喜欢摄影"
    if label=="cancel_preference": return len(ops)==1 and ops[0]["action"]=="delete" and ops[0]["id"]==22
    if label=="scoped_authorization":
        return any(op.get("fact",{}).get("memoryMode")=="explicit" and numeric_quantity(op["fact"]["value"])==numeric_quantity("两只") for op in ops) and all("杯子" not in json.dumps(op,ensure_ascii=False) for op in ops)
    if label=="profile": return len(ops)==2 and fields=={"age","student_status"}
    if label=="interests": return len(ops)==2 and {op["fact"]["value"] for op in ops}=={"喜欢摄影","喜欢游泳"}
    if label=="pets": return len(ops)==1 and fields=={"pet_kind"} and ops[0]["fact"]["value"]=="猫"
    if label=="explicit": return any(op["fact"].get("memoryMode")=="explicit" and numeric_quantity(op["fact"]["value"])==numeric_quantity("两只") for op in ops)
    if label=="followup": return len(ops)==1 and ops[0]["action"]=="update" and ops[0]["id"]==11 and numeric_quantity(ops[0]["fact"]["value"])==numeric_quantity("三只")
    return False


async def main():
    config=load_config(); selected=config["Memory"][config["selected_module"]["Memory"]]
    name=selected.get("llm") or config["selected_module"]["LLM"]
    settings={**config["LLM"][name],"timeout":30}
    llm=create_instance(settings.get("type",name),settings)
    if hasattr(llm,"client") and hasattr(llm.client,"with_options"):
        llm.client=llm.client.with_options(max_retries=0)
    cases=[("long_goal","我准备半年内考过六级",[]),
           ("stable_habit","我每天晚上跑步",[]),
           ("two_named_pets","我有两只猫，一只叫豆包，一只叫雪球",[]),
           ("temporary_run","我今天晚上去跑步",[]),
           ("temporary_game","我等下想打游戏",[]),
           ("hypothetical_pet","如果以后我养猫，我想叫它豆包",[]),
           ("friend_pet","朋友的猫叫豆包",[]),
           ("negated_habit","我并不是每天晚上跑步",[]),
           ("aspirational_habit","我希望以后每天晚上跑步，但现在没开始，也没有决定什么时候开始",[]),
           ("profile","我是大学生今年21岁",[]),
           ("interests","我喜欢摄影和游泳",[]),
           ("pets","我有两只猫",[]),
           ("transient","我想出门",[]),
           ("purchase","我在市场买了一个杯子",[]),
           ("travel","我计划去日本旅游",[]),
           ("negated","不要记住我有两只猫",[]),
           ("explicit","请记住我有两只猫",[]),
           ("followup","我又养了一只猫",[opted_count()]),
           ("refuse_age","不要记住我今年21岁",[]),
           ("refuse_suffix","我今年21岁，这次不要保存",[]),
           ("scoped_authorization","请记住我有两只猫，我有三个杯子",[]),
           ("repeat_preference","我的兴趣是摄影",[liked_photography()]),
           ("negative_preference","我现在不喜欢摄影",[liked_photography()]),
           ("cancel_preference","我不再喜欢摄影",[liked_photography()])]
    if len(sys.argv)>1: cases=[c for c in cases if c[0] in sys.argv[1:]]
    if not cases: raise SystemExit("Unknown case")
    failed=[]
    try:
        for label,text,memories in cases:
            provider=MemoryProvider({}); provider.init_memory("synthetic-test",llm)
            diagnostics=[]
            provider._diagnose=lambda phase,reason,fact=None: diagnostics.append((phase,str(reason)))
            original_ask=provider._ask_json
            raw_responses=[]
            async def traced_ask(prompt,data,tokens=1500):
                result=await original_ask(prompt,data,tokens)
                raw_responses.append(result)
                return result
            provider._ask_json=traced_ask
            writer=AsyncMock(return_value=dict(created=0,updated=0,deleted=0))
            snapshot=dict(schemaVersion=2,retrievalVersion=1,roleId="synthetic-test",revision=5,memories=memories)
            with patch(MODULE+".search_memory_facts",AsyncMock(return_value=snapshot)),patch(MODULE+".commit_memory_facts",writer),patch(MODULE+".logger",Mock()):
                ops=await provider.save_memory([NS(role="user",content=text,created_at=WHEN,uniq_id=label)],"synthetic-session")
            passed=valid(label,ops)
            print(label,"PASS" if passed else "FAIL",json.dumps(ops,ensure_ascii=False),flush=True)
            if not passed:
                print("diagnostics:",diagnostics,flush=True)
                print("synthetic_model_responses:",json.dumps(raw_responses,ensure_ascii=False),flush=True)
                failed.append(label)
    finally:
        if hasattr(llm,"client"): llm.client.close()
    print(f"Controlled smoke: {len(cases)-len(failed)}/{len(cases)} (not a production success-rate estimate)",flush=True)
    original={"profile","interests","pets","transient","purchase","travel","negated","explicit","followup"}
    for group,names in (("Original nine",original),("New boundaries",{c[0] for c in cases}-original)):
        selected=[c[0] for c in cases if c[0] in names]
        if selected: print(f"{group}: {len(selected)-len(set(selected)&set(failed))}/{len(selected)}",flush=True)
    if failed: raise SystemExit("Failed: "+", ".join(failed))


if __name__=="__main__": asyncio.run(main())
