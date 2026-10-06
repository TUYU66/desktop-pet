"""手动验收集合记忆的真实模型表现；仅合成会话，数据库读写均替换为内存桩。"""
import asyncio
import copy
import json
from unittest.mock import AsyncMock, patch

from check_memory_live import load_config, create_instance, MemoryProvider, NS, WHEN, MODULE
from core.providers.memory.mem_local_short.facts import decode_fact, numeric_quantity


async def main():
    config=load_config()
    selected=config["Memory"][config["selected_module"]["Memory"]]
    name=selected.get("llm") or config["selected_module"]["LLM"]
    settings={**config["LLM"][name],"timeout":30}
    llm=create_instance(settings.get("type",name),settings)
    provider=MemoryProvider({}); provider.init_memory("synthetic-aggregates",llm)
    memories=[]; context=[]; revision=1; next_id=1

    async def search(*args,**kwargs):
        return dict(schemaVersion=2,retrievalVersion=1,writeProtocolVersion=3,roleId="synthetic-aggregates",
                    revision=revision,memories=copy.deepcopy(memories),truncated=False)

    async def commit(role,session,turn,expected,ops):
        nonlocal revision,next_id
        if expected!=revision: return None
        counts=dict(created=0,updated=0,deleted=0)
        for op in ops:
            if op["action"]=="add":
                memories.append(dict(id=next_id,category=op["category"],version=1,
                    content="合成验收记录",factJson=json.dumps(op["fact"],ensure_ascii=False)))
                next_id+=1; counts["created"]+=1
            else:
                row=next(m for m in memories if m["id"]==op["id"])
                if op["action"]=="delete":
                    memories.remove(row); counts["deleted"]+=1
                else:
                    row.update(category=op["category"],version=row["version"]+1,
                               factJson=json.dumps(op["fact"],ensure_ascii=False))
                    counts["updated"]+=1
        revision+=1
        return counts

    async def turn(text):
        message=NS(role="user",content=text,created_at=WHEN,uniq_id="aggregate-"+str(len(context)))
        ops=await provider.save_memory([message],"synthetic-session",user_context=context[-6:])
        context.append(message)
        print(text,ops,flush=True)
        assert ops is not None,"记忆流程失败，不能视为正确跳过"
        return ops

    def collection(subject):
        rows=[m for m in memories if decode_fact(m).get("subject")==subject
              and decode_fact(m).get("predicate")=="数量"]
        assert len(rows)==1,(subject,rows)
        return rows[0],decode_fact(rows[0])

    try:
        with patch(MODULE+".search_memory_facts",AsyncMock(side_effect=search)), \
             patch(MODULE+".commit_memory_facts",AsyncMock(side_effect=commit)):
            await turn("我有三只猫两条狗")
            cat_row,cat=collection("用户的猫"); dog_row,dog=collection("用户的狗")
            assert numeric_quantity(cat["value"])==(3,"只")
            assert numeric_quantity(dog["value"])==(2,"条")
            assert cat["entityId"]!=dog["entityId"]
            assert cat["predicateId"]==dog["predicateId"]
            dog_before=copy.deepcopy(dog_row)

            ops=await turn("我又养了一只猫")
            assert len(ops)==1 and ops[0]["action"]=="update" and ops[0]["id"]==cat_row["id"]
            _,cat=collection("用户的猫")
            assert numeric_quantity(cat["value"])==(4,"只")
            assert collection("用户的狗")[0]==dog_before

            before=copy.deepcopy(memories)
            ops=await turn("其中一只猫叫小花")
            assert ops and all(op["action"]=="add" for op in ops)
            individuals=[op["fact"] for op in ops if "小花" in op["fact"]["subject"]]
            assert individuals and len({f["entityId"] for f in individuals})==1
            assert all(f["entityId"] not in {cat["entityId"],dog["entityId"]} for f in individuals)
            assert all(row in memories for row in before),"命名单只宠物不能改变集合记录"

            before=copy.deepcopy(memories)
            assert await turn("如果以后养猫我想叫小花")==[]
            assert memories==before

            before=copy.deepcopy(memories)
            ops=await turn("我朋友有两只猫")
            assert all(op["action"]=="add" for op in ops)
            assert all(op["fact"]["entityId"] not in {cat["entityId"],dog["entityId"],"user"} for op in ops)
            assert all(row in memories for row in before),"朋友的宠物不能覆盖用户集合"
        print("PASS: 集合拆分、相对数量、个体命名、假设和归属",flush=True)
    finally:
        if hasattr(llm,"client"): llm.client.close()


if __name__=="__main__": asyncio.run(main())
