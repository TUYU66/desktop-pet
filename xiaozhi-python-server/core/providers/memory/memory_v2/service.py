"""写入编排只有两个模型阶段；任何失败均不降级到旧流程。"""
from .extractor import extract
from .retriever import retrieve
from .decision import decide
from .validator import validate
from copy import deepcopy
from .models import CommitConflict, CommitUnconfirmed, SaveResult


class MemoryService:
    def __init__(self, model, store, log=lambda *_: None):
        self.model, self.store, self.log = model, store, log

    async def save(self, turn, remember_commit=lambda payload: None):
        intents = await extract(self.model, turn)
        self.log("retrieval_intent", {"messageId": turn.message_id, "items": len(intents)})
        if not intents:
            return SaveResult("skipped")
        batch = await retrieve(self.store, turn.role_id, intents)
        self.log("retrieved", {"messageId": turn.message_id, "ids": [r["id"] for r in batch.records],
                               "revision": batch.revision, "omitted": batch.omitted})
        answer = await decide(self.model, turn, batch)
        plan = validate(answer, turn, batch)
        if plan.uncertain:
            return SaveResult("uncertain")
        if not plan.operations:
            return SaveResult("skipped")
        payload = {"roleId": turn.role_id, "writeProtocolVersion": 4,
            "turnId": turn.message_id, "sessionId": turn.session_id, "source": turn.latest_user,
            "observedAt": turn.observed_at, "expectedRevision": batch.revision, "operations": list(plan.operations)}
        # 在任何网络调用之前保存原提案，响应丢失时禁止重新抽取/决策。
        remember_commit(deepcopy(payload))
        return await self.commit(payload)

    async def commit(self, payload):
        try:
            counts = await self.store.commit(deepcopy(payload))
        except CommitConflict:
            raise
        except Exception as exc:
            raise CommitUnconfirmed("commit_unconfirmed") from exc
        if not isinstance(counts, dict) or any(type(counts.get(k)) is not int or counts[k] < 0 for k in ("created", "updated", "deleted")):
            raise CommitUnconfirmed("commit_unconfirmed")
        self.log("committed", {"messageId": payload["turnId"], "counts": counts})
        return SaveResult("committed", tuple(payload["operations"]), counts)
