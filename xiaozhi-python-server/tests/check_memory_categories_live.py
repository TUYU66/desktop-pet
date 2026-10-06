"""大类策略真实模型验收入口：仅合成原话，数据库读写全部隔离；需手动运行。"""
import asyncio
from unittest.mock import AsyncMock, patch
from check_memory_live import load_config, create_instance, MemoryProvider, NS, WHEN, MODULE


CASES = [
    ("handedness", "我是左撇子", "habit", "惯用手"),
    ("goal", "我准备半年内考过六级", "goal", None),
    ("habit", "我每天晚上跑步", "habit", None),
    ("temporary", "我今天晚上去跑步", None, None),
    ("hypothetical", "如果以后养猫，我想叫它豆包", None, None),
    ("negative", "我不喜欢摄影", "preference", None),
    ("explicit_categorized", "请记住我是左撇子", "habit", "惯用手"),
    ("explicit_note", "请记住我们私下约定的口头暗号是蓝色纸鹤", "note", None),
    ("sensitive", "请记住我的密码是hunter123", None, None),
]


async def main():
    config=load_config()
    selected=config["Memory"][config["selected_module"]["Memory"]]
    name=selected.get("llm") or config["selected_module"]["LLM"]
    settings={**config["LLM"][name],"timeout":30}
    llm=create_instance(settings.get("type",name),settings)
    failed=[]
    try:
        for label,text,category,predicate in CASES:
            provider=MemoryProvider({}); provider.init_memory("synthetic-categories",llm)
            snapshot=dict(schemaVersion=2,retrievalVersion=1,writeProtocolVersion=3,roleId="synthetic-categories",revision=1,memories=[],truncated=False)
            with patch(MODULE+".search_memory_facts",AsyncMock(return_value=snapshot)), patch(MODULE+".commit_memory_facts",AsyncMock(return_value=dict(created=1,updated=0,deleted=0))):
                ops=await provider.save_memory([NS(role="user",content=text,created_at=WHEN,uniq_id=label)],"synthetic-session")
            passed=ops==[] if category is None else bool(ops) and all(op.get("category")==category for op in ops)
            if passed and predicate:
                passed=any(op.get("fact",{}).get("predicate")==predicate for op in ops)
            if passed and label=="negative":
                passed=all("不喜欢" in op.get("fact",{}).get("value","") for op in ops)
            if passed and category=="note":
                passed=all(op.get("fact",{}).get("memoryMode")=="explicit" for op in ops)
            print(label,"PASS" if passed else "FAIL",ops,flush=True)
            if not passed: failed.append(label)
    finally:
        if hasattr(llm,"client"): llm.client.close()
    if failed: raise SystemExit("Failed: "+", ".join(failed))


if __name__=="__main__":
    asyncio.run(main())
