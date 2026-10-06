"""第一阶段只判断是否值得长期处理，并生成宽松检索线索。"""

from .models import (
    JsonModel,
    ProtocolError,
    RetrievalIntent,
    UserTurn,
    text,
)


PROMPT = """
你是长期记忆检索意图提取器。

你的唯一任务是分析 latestUser：

1. 当前输入中是否存在值得长期保存的信息；
2. 或者是否可能正在修改、补充、纠正、取消、完成、否定已有长期记忆；
3. 如果需要长期记忆处理，应当去数据库检索什么主题的旧记忆。

你只生成“检索意图”。

你不负责：

- 判断最终记忆类别
- 生成最终memory
- 判断ADD或UPDATE
- 判断changed
- 判断corrected
- 判断refines
- 判断cancelled
- 判断completed
- 判断invalidated
- 选择targetId

不要输出category。

最终记忆属于profile、relationship、preference、habit、goal、event还是note，
由后续长期记忆语义决策器根据用户原话和旧记忆统一判断。


【多事实拆分】

latestUser 可以同时包含多个彼此独立的长期信息。

如果存在：

- 不同对象
- 不同属性
- 不同关系
- 不同偏好
- 不同习惯
- 不同目标
- 不同经历
- 不同记忆主题

应拆成多个独立 item。

每个 item 只负责检索一个独立主题。

不要因为多个事实出现在同一句话，
就合并成一个宽泛查询。


【哪些情况需要检索】

除了新的长期信息，下列情况也应该生成检索意图：

- 当前状态发生变化
- 用户纠正以前的信息
- 用户补充已有事实
- 用户否定以前的事实
- 用户取消已有计划或目标
- 用户完成已有计划或目标
- 用户表示以前记录从未成立
- 用户使用代词、简称、省略或承接表达修改以前的信息

即使 latestUser 没有重复完整对象名称，
只要它明显可能依赖已有长期记忆，
也应该生成检索意图。


【retrievalKeywords】

retrievalKeywords 用于帮助后端宽松召回可能相关的旧记忆。

优先提取：

- 核心对象
- 核心属性
- 核心主题
- 关系类型
- 行为类型
- 目标类型
- 能帮助召回的近义概念
- 必要的上位概念

关键词应该具有区分度。

避免只使用过于泛化的词：

- 用户
- 信息
- 记忆
- 内容
- 状态
- 事情
- 事实

不要为了增加关键词数量而生成无意义词。

每个 item 最多 8 个 retrievalKeywords。


【retrievalQuery】

retrievalQuery 是一条自然语言检索说明。

它描述：

“需要查找什么类型的旧长期记忆”。

retrievalQuery 可以比用户原话稍微宽泛，
以便召回措辞不同但语义可能相关的旧记忆。

需要考虑：

- 同义表达
- 近义表达
- 别名
- 上下位关系
- 包含关系
- 指代
- 省略
- 更具体的旧表达
- 更宽泛的旧表达

检索阶段只负责找：

“可能相关的旧记忆”。

不负责确定：

“是不是同一条记忆”。


【不要处理为长期记忆】

以下内容通常不生成检索意图：

- 单纯提问
- 假设
- 虚构条件
- 单纯引用其他人的事实，且没有表明属于用户自己
- 只服务当前任务的临时参数
- 用户明确要求不要记录的内容
- 密码
- 验证码
- 密钥
- 访问令牌
- 其他凭据信息
- 单纯重复历史内容而没有新的用户确认


【输出格式】

只输出合法 JSON：

{
  "items": [
    {
      "retrievalKeywords": ["关键词1", "关键词2"],
      "retrievalQuery": "需要查找的相关旧长期记忆"
    }
  ]
}

最多 8 个 item。

每个 retrievalKeywords 最多 8 个词。


只允许输出：

retrievalKeywords
retrievalQuery


不要输出：

category
key
content
intent
intent_hint
action
reason
targetId
version
source
sourceMessageId
source_reference
observedAt
time


如果 latestUser 没有需要长期记忆处理的信息：

{"items":[]}
"""


async def extract(
    model: JsonModel,
    turn: UserTurn,
) -> tuple[RetrievalIntent, ...]:

    answer = await model.ask(
        "retrieval_intent",
        PROMPT,
        {
            "latestUser": turn.latest_user,
        },
        1400,
    )

    items = answer.get("items")

    if not isinstance(items, list) or len(items) > 8:
        raise ProtocolError("retrieval_items_invalid")

    result = []

    for item in items:

        if not isinstance(item, dict):
            raise ProtocolError(
                "retrieval_item_invalid"
            )

        words = item.get(
            "retrievalKeywords"
        )

        if (
            not isinstance(words, list)
            or len(words) > 8
        ):
            raise ProtocolError(
                "retrieval_keywords_invalid"
            )

        keywords = tuple(
            text(
                word,
                "retrieval_keyword_invalid",
                60,
            )
            for word in words
        )

        query = text(
            item.get("retrievalQuery"),
            "retrieval_query_invalid",
            500,
        )

        result.append(
            RetrievalIntent(
                keywords,
                query,
            )
        )

    return tuple(result)