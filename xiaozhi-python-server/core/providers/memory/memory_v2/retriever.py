"""读取现有后端快照并进行宽松候选召回。

Retriever只回答“哪些旧记忆可能相关”。

它不选择最终target，
不判断ADD/UPDATE，
不理解changed/corrected/cancelled等状态语义。

没有足够相关证据时允许返回空候选。
"""

import json
import re
import unicodedata

from .models import (
    CATEGORIES,
    MemoryStore,
    ProtocolError,
    StoreUnavailable,
    RecallBatch,
    integer,
    text,
)


def normalized(value):
    return "".join(
        unicodedata.normalize(
            "NFKC",
            str(value or ""),
        )
        .casefold()
        .split()
    )


def tokens(value):
    """
    中文保留相邻双字；
    英文/数字保留完整token。

    单字仍保留在基础tokens中，
    但语义重叠计算时主要使用双字以上token，
    降低中文常见单字造成的噪声。
    """
    parts = re.findall(
        r"[a-z0-9]+|[\u4e00-\u9fff]",
        normalized(value),
    )

    result = set(parts)

    result.update(
        a + b
        for a, b in zip(parts, parts[1:])
    )

    return result


# 这些词本身区分度太低，
# 不能单独证明两条记忆相关。
_GENERIC_TERMS = frozenset(
    normalized(v)
    for v in {
        "用户",
        "个人",
        "信息",
        "相关",
        "旧记忆",
        "长期记忆",
        "记忆",
        "内容",
        "事实",
        "主题",
        "属性",
        "状态",
        "关系",
        "偏好",
        "喜欢",
        "习惯",
        "目标",
        "事件",
        "经历",
        "计划",
        "数量",
        "行为",
    }
)


def semantic_tokens(value):
    """
    用于弱语义重叠。

    中文单字很容易造成随机碰撞，
    因此这里只使用长度 >= 2 的token。
    """
    return {
        token
        for token in tokens(value)
        if len(token) >= 2
        and token not in _GENERIC_TERMS
    }


def specific_keywords(intent):
    """
    从LLM提供的关键词中去除过于泛化的词。

    单字对象仍然允许作为关键词，
    因为它是LLM明确给出的核心检索线索，
    和自然文本自动拆出的单字不同。
    """
    result = []

    for keyword in intent.keywords:
        value = normalized(keyword)

        if not value:
            continue

        if value in _GENERIC_TERMS:
            continue

        result.append(value)

    return tuple(result)


def relevance_score(row, intent):
    """
    返回：

        float -> 有候选价值
        None  -> 没有足够相关证据，不召回

    Retriever不判断category。

    这里只根据第一次LLM提供的：
    - retrievalKeywords
    - retrievalQuery

    与旧记忆的：
    - key
    - content
    - source

    判断是否值得作为候选交给Decision。
    """

    corpus = " ".join(
        str(row.get(name, ""))
        for name in (
            "key",
            "content",
            "source",
        )
    )

    corpus_norm = normalized(corpus)

    keywords = specific_keywords(intent)

    keyword_hits = sum(
        1
        for keyword in keywords
        if keyword in corpus_norm
    )

    query_tokens = semantic_tokens(
        intent.query
    )

    corpus_tokens = semantic_tokens(
        corpus
    )

    overlap = len(
        query_tokens
        & corpus_tokens
    )

    # ---------------------------------------------
    # 最低准入门槛
    #
    # 完全没有文本相关信号：
    # 不召回。
    #
    # 数据库有记录 ≠ 一定存在相关旧记忆。
    # ---------------------------------------------

    if (
        keyword_hits == 0
        and overlap == 0
    ):
        return None

    score = 0.0

    # LLM明确给出的关键词命中权重最高。
    score += (
        keyword_hits
        * 6.0
    )

    # retrievalQuery 和旧记忆的文本重叠。
    score += (
        overlap
        * 1.5
    )

    # 关键词覆盖程度。
    if keywords:
        score += (
            keyword_hits
            / len(keywords)
        )

    return score


def read_snapshot(snapshot):
    try:
        return _read_snapshot(snapshot)
    except ProtocolError as exc:
        raise StoreUnavailable(str(exc)) from exc


def _read_snapshot(snapshot):
    if (
        not isinstance(snapshot, dict)
        or not isinstance(
            snapshot.get("memories"),
            list,
        )
    ):
        raise ProtocolError(
            "snapshot_unavailable"
        )

    revision = integer(
        snapshot.get("revision"),
        "snapshot_revision_invalid",
        0,
    )

    rows = []
    ids = set()

    for raw in snapshot["memories"]:
        if not isinstance(raw, dict):
            raise ProtocolError(
                "snapshot_record_invalid"
            )

        ident = integer(
            raw.get("id"),
            "snapshot_id_invalid",
        )

        if (
            ident in ids
            or raw.get("category")
            not in CATEGORIES
            or raw.get("status")
            not in {
                "active",
                "closed",
                "invalidated",
            }
        ):
            raise ProtocolError(
                "snapshot_record_invalid"
            )

        ids.add(ident)

        row = {
            "id": ident,
            "version": integer(
                raw.get("version"),
                "snapshot_version_invalid",
            ),
            "category": raw["category"],
            "key": text(
                raw.get("key"),
                "snapshot_key_invalid",
                120,
            ),
            "content": text(
                raw.get("content"),
                "snapshot_content_invalid",
                2000,
            ),
            "source": text(
                raw.get("source"),
                "snapshot_source_invalid",
                2000,
            ),
            "status": raw["status"],
        }

        for name in (
            "observedAt",
            "time",
        ):
            if name in raw:
                row[name] = raw[name]

        rows.append(row)

    return revision, tuple(rows)


async def retrieve(
    store: MemoryStore,
    role_id,
    intents,
    limit=16,
    char_budget=9000,
) -> RecallBatch:

    revision, records = read_snapshot(
        await store.snapshot(role_id)
    )

    eligible = [
        record
        for record in records
        if record["status"] != "invalidated"
    ]

    # 每个intent建立自己的“真正相关候选队列”。
    queues = []

    positive_ids = set()

    for intent in intents:
        scored = []

        for row in eligible:
            score = relevance_score(
                row,
                intent,
            )

            if score is None:
                continue

            positive_ids.add(row["id"])

            scored.append(
                (
                    score,
                    row,
                )
            )

        scored.sort(
            key=lambda item: (
                -item[0],
                item[1]["id"],
            )
        )

        queues.append(
            [
                row
                for _score, row in scored
            ]
        )

    # --------------------------------------------------
    # 很重要：
    #
    # 如果没有任何记录达到最低相关要求，
    # relatedMemories 就应该为空。
    #
    # 数据库存在记忆 ≠ 当前一定有相关旧记忆。
    # --------------------------------------------------

    if not positive_ids:
        return RecallBatch(
            revision,
            (),
            records,
            0,
        )

    selected = []
    seen = set()
    used = 0

    max_depth = max(
        (
            len(queue)
            for queue in queues
        ),
        default=0,
    )

    # 多个检索主题轮流贡献候选，
    # 防止单一主题占满全部预算。
    for offset in range(max_depth):
        for queue in queues:
            if offset >= len(queue):
                continue

            row = queue[offset]

            if row["id"] in seen:
                continue

            size = len(
                json.dumps(
                    row,
                    ensure_ascii=False,
                )
            )

            if (
                used + size
                > char_budget
                or len(selected) >= limit
            ):
                continue

            seen.add(row["id"])

            selected.append(row)

            used += size

        if len(selected) >= limit:
            break

    # omitted只表示：
    # “真正相关但因为limit/budget未展示”的数量。
    #
    # 不再把数据库里所有无关记忆算进去。
    omitted = max(
        0,
        len(positive_ids)
        - len(selected),
    )

    return RecallBatch(
        revision,
        tuple(selected),
        records,
        omitted,
    )
