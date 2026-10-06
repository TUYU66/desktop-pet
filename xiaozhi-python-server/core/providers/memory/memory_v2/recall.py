"""读取已提交记忆；不调用旧模块，也不触发写入模型。"""
import asyncio
import json
import re
from .models import RetrievalIntent
from .retriever import read_snapshot, retrieve


def query_keywords(query):
    """只为读取补充词法检索线索，不解释写入意图或绑定目标。

    去掉问句功能词后保留完整片段，包括单字名词；不把所有单字
    都放入关键词，从而避免依靠“我/有/是”等公共字召回无关记录。
    """
    chunks = re.split(
        r"(?:请问|告诉我|还记得|记得|多少|几个|几只|什么|怎么样|如何|有没有|是不是)"
        r"|[我你他她它的了呢吗啊呀有是都在几只个这那，。？！、\s]+",
        query or "",
    )
    return tuple(dict.fromkeys(part.strip() for part in chunks if part.strip()))[:16]


async def recall(
    store,
    role_id,
    query,
    list_all=False,
):

    if list_all:

        _, rows = read_snapshot(
            await store.snapshot(role_id)
        )

    else:

        batch = await retrieve(
            store,
            role_id,
            (
                RetrievalIntent(
                    query_keywords(query),
                    query
                    or "用户长期记忆",
                ),
            ),
            limit=12,
        )

        rows = batch.records

    rows = [
        row
        for row in rows
        if row["status"]
        != "invalidated"
    ]

    if not rows:
        return "没有检索到相关长期记忆。"

    lines = [
        "以下是已保存记忆。"
        "closed表示已结束，不是当前目标；"
        "陈述日期不能用于自动推算年龄。"
    ]

    used = len(lines[0])

    def append(line):
        nonlocal used

        if (
            used
            + len(line)
            + 1
            > 5900
        ):
            lines.append(
                "【仅展示部分记忆，"
                "完整记录请查看网页】"
            )
            return False

        lines.append(line)

        used += (
            len(line)
            + 1
        )

        return True

    histories = {}
    if not list_all:
        slots = asyncio.Semaphore(4)
        async def read_history(row):
            async with slots:
                histories[row["id"]] = await store.history(role_id, row["id"])
        await asyncio.gather(*(read_history(row) for row in rows))

    for row in rows:

        if not append(
            f"- [{row['status']}; "
            f"陈述于{row.get('observedAt', '未知')}] "
            f"{row['content']}"
        ):
            break

        if not list_all:

            history = histories[row["id"]]

            if history is None:

                if not append(
                    "  历史暂时不可用，"
                    "不能推断没有过去状态。"
                ):
                    break

            for entry in history or []:

                if (
                    entry.get("reason")
                    not in {
                        "changed",
                        "cancelled",
                        "completed",
                    }
                    or not entry.get(
                        "oldJson"
                    )
                ):
                    continue

                previous = json.loads(
                    entry["oldJson"]
                )

                if (
                    previous.get("status")
                    == "active"
                ):

                    if not append(
                        "  曾经（陈述于"
                        f"{previous.get('observedAt', '未知')}"
                        f"）：{previous['content']}"
                    ):
                        return "\n".join(
                            lines
                        )

    return "\n".join(lines)
