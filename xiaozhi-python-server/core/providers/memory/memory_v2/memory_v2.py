"""生产入口适配器。

旧mem_local_short不参与V2执行。
"""

import asyncio
import hashlib

from collections import OrderedDict
from datetime import datetime

from ..base import (
    MemoryProviderBase,
    logger,
)

from .models import (
    CommitConflict,
    ProtocolError,
    SaveResult,
    UserTurn,
)
from .runtime import (
    ModelRuntime,
    diagnostic_text,
)
from .service import MemoryService
from .store import JavaMemoryStore
from .recall import recall


class MemoryProvider(MemoryProviderBase):

    uses_memory_v2 = True

    def __init__(
        self,
        config,
        summary_memory=None,
    ):
        super().__init__(config)

        self.llm = None

        self.short_memory = ""

        self.store = JavaMemoryStore()

        self._lock = asyncio.Lock()

        self._receipts = OrderedDict()
        # 未确认事务不参与 receipt 淘汰，避免淘汰后重新生成提案。
        self._pending_commits = {}


    def _log(
        self,
        stage,
        data,
    ):
        logger.bind(
            tag=__name__
        ).info(
            "memory_v2 "
            + stage
            + " "
            + diagnostic_text(data)
        )


    async def _process(
        self,
        messages,
        session_id,
        checkpoint=None,
    ):

        current = next(
            (
                message
                for message
                in reversed(
                    messages or []
                )
                if (
                    getattr(
                        message,
                        "role",
                        None,
                    )
                    == "user"
                    and getattr(
                        message,
                        "is_user_input",
                        True,
                    )
                    and not getattr(
                        message,
                        "is_temporary",
                        False,
                    )
                    and getattr(
                        message,
                        "content",
                        None,
                    )
                )
            ),
            None,
        )

        if (
            current is None
            or self.llm is None
        ):
            return None

        # 正常应用消息应该具有uniq_id。
        #
        # 极少数没有ID的调用，
        # 使用当前消息对象身份构造稳定的本轮ID，
        # 防止不同轮中内容完全相同的消息被误合并。
        message_id = str(
            getattr(
                current,
                "uniq_id",
                None,
            )
            or hashlib.sha256(
                (
                    f"{session_id}:"
                    f"{id(current)}"
                ).encode()
            ).hexdigest()
        )

        receipt_key = (
            self.role_id,
            session_id
            or self.session_id,
            message_id,
        )

        async with self._lock:

            # 只有已经得到“确定结果”的轮次
            # 才会存在receipt。
            if receipt_key in self._receipts:
                return self._receipts[
                    receipt_key
                ]

            try:
                turn = UserTurn.create(
                    current.content,
                    message_id,
                    getattr(
                        current,
                        "created_at",
                        None,
                    )
                    or datetime.now().astimezone(),
                    self.role_id,
                    session_id
                    or self.session_id,
                )

                model = ModelRuntime(
                    self.llm,
                    self.config,
                    lambda stage, data: self._log(
                        stage,
                        {
                            "messageId": message_id,
                            "data": data,
                        },
                    ),
                )

                service = MemoryService(
                    model,
                    self.store,
                    self._log,
                )
                if receipt_key in self._pending_commits:
                    self._log("commit_retry", {"messageId": message_id})
                    result = await service.commit(self._pending_commits[receipt_key])
                else:
                    def remember(payload):
                        if checkpoint:
                            checkpoint(payload)
                        self._pending_commits[receipt_key] = payload
                    result = await service.save(turn, remember)

            except CommitConflict as exc:
                self._log("commit_conflict", {"messageId": message_id, "error": str(exc)[:250]})
                result = SaveResult("conflict")

            except ProtocolError as exc:
                # Validator/协议拒绝是确定性结果。
                #
                # 同一个messageId不能再次让LLM重抽答案，
                # 否则可能通过随机输出绕过Validator。
                self._log(
                    "rejected",
                    {
                        "messageId": message_id,
                        "type": type(exc).__name__,
                        "error": str(exc)[:250],
                    },
                )

                result = SaveResult(
                    "rejected"
                )

            except Exception as exc:
                # 网络、模型服务、数据库等临时故障：
                # 不缓存，允许后续真正重试。
                self._log(
                    "failed",
                    {
                        "messageId": message_id,
                        "type": type(exc).__name__,
                        "error": str(exc)[:250],
                    },
                )

                if receipt_key in self._pending_commits:
                    self._invalidate_recall()
                return None

            # committed / skipped / uncertain / rejected
            # 都属于已经确定处理结果的轮次。
            self._pending_commits.pop(receipt_key, None)
            if result.status in {"committed", "conflict"}:
                self._invalidate_recall()
            self._receipts[
                receipt_key
            ] = result

            if len(self._receipts) > 256:
                self._receipts.popitem(
                    last=False
                )

            return result


    async def save_memory(
        self,
        msgs,
        session_id=None,
        user_context=None,
    ):

        result = await self._process(
            msgs,
            session_id,
        )

        return (
            list(result.operations)
            if result
            else None
        )


    async def prepare_memory_feedback(
        self,
        msgs,
        session_id=None,
        user_context=None,
    ):

        # 回复阶段与保存阶段使用同一receipt。
        #
        # 如果保存已经确定完成，
        # 不再重新运行LLM判断。
        result = await self._process(
            msgs,
            session_id,
        )

        return self._feedback(result)

    @staticmethod
    def _feedback(result):

        if result is None or result.status == "failed":
            return (
                "本轮长期记忆尚未确认保存成功，"
                "不要声称已经保存。"
            )
        if result.status == "rejected":
            return (
                "本轮长期记忆未通过安全校验，"
                "数据库没有被修改。"
            )
        if result.status == "conflict":
            return "本轮长期记忆提交存在版本或状态冲突，未确认保存成功；不要声称已完成修改。"
        if result.status == "uncertain":
            return (
                "本轮记忆目标或含义不确定，"
                "未修改数据库。"
            )

        if not result.operations:
            return None

        counts = result.counts or {}

        return (
            "本轮长期记忆事务已确认："
            f"新增{counts.get('created', 0)}条，"
            f"更新{counts.get('updated', 0)}条，"
            f"作废{counts.get('deleted', 0)}条。"
        )

    def _invalidate_recall(self):
        from .background import scope_key
        from .cache import get_cache
        get_cache(scope_key(self.role_id)).invalidate()

    async def initialize_background(self):
        from .background import scope_key, get_queue
        from .cache import get_cache
        scope = scope_key(self.role_id)
        get_queue().bind(scope, self)
        try:
            await asyncio.wait_for(get_cache(scope).snapshot(self.store, self.role_id), 3)
        except Exception as error:
            self._log('cache_warm_failed', {'type': type(error).__name__})

    async def prepare_chat_memory(self, message, session_id):
        from .background import scope_key, get_queue, trivial, needs_confirmation
        from core.utils.dialogue import saved_memory_recall_answer
        if not isinstance(message.content, str) or not message.content.strip():
            return None
        if saved_memory_recall_answer(message.content, "") is not None:
            return None  # A pure list request reads committed data; it is not a write job.
        if trivial(message.content):
            self._log('trivial_skipped', {'messageId': message.uniq_id})
            return None
        try:
            future = get_queue().enqueue(scope_key(self.role_id), self, message, session_id)
        except Exception as error:
            self._log('enqueue_failed', {'messageId': message.uniq_id, 'type': type(error).__name__})
            return self._feedback(None)
        if not needs_confirmation(message.content):
            return '本轮信息已进入后台记忆队列，尚未确认保存。正常回答用户，不主动汇报后台过程，也不要声称已保存。'
        try:
            result = await asyncio.wait_for(asyncio.shield(future), 12)
        except asyncio.TimeoutError:
            return self._feedback(None)
        return self._feedback(result) or '本轮未产生新的记忆操作，不能声称已新增或修改记忆。'


    async def _cached_recall(self, query):
        from core.utils.dialogue import saved_memory_recall_answer
        from .background import scope_key
        from .cache import get_cache
        cache = get_cache(scope_key(self.role_id))
        list_all = saved_memory_recall_answer(query, "") is not None
        snapshot = await cache.snapshot(self.store, self.role_id or "default", fresh=list_all)
        return await recall(cache.view(self.store, snapshot), self.role_id or "default", query, list_all)

    async def query_memory(
        self,
        query,
    ):

        from core.utils.dialogue import (
            saved_memory_recall_answer,
        )

        try:
            self.short_memory = await asyncio.wait_for(self._cached_recall(query), 3)

        except Exception as exc:

            self._log(
                "recall_failed",
                {
                    "type": type(
                        exc
                    ).__name__,
                },
            )

            self.short_memory = (
                "长期记忆数据库本轮读取失败；"
                "不要编造记忆。"
            )

        return self.short_memory


    async def load_memory_from_db(
        self,
        _session_id=None,
    ):

        return await self.query_memory(
            "查看我的长期记忆"
        )
