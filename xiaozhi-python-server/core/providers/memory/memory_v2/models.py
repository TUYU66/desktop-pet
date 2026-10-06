"""V2边界模型；来源和时间只接受应用传入的消息。"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

CATEGORIES = frozenset({"profile", "relationship", "preference", "habit", "goal", "event", "note"})


class ProtocolError(ValueError):
    pass


class StoreUnavailable(RuntimeError):
    """读取失败或响应不可用，不是用户提案被拒绝。"""


class CommitUnconfirmed(RuntimeError):
    """提交结果未知；只能重试原始事务。"""


class CommitConflict(RuntimeError):
    """后端明确拒绝过期或冲突的事务。"""


def text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ProtocolError(name)
    return value.strip()


def integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ProtocolError(name)
    return value


@dataclass(frozen=True)
class UserTurn:
    latest_user: str
    message_id: str
    observed_at: str
    role_id: str
    session_id: str

    @classmethod
    def create(cls, latest_user, message_id, timestamp: datetime, role_id, session_id):
        text(latest_user, "latest_user_invalid", 2000)
        return cls(latest_user, text(message_id, "message_id_invalid", 160),
                   timestamp.astimezone().isoformat(timespec="seconds"), role_id or "default", session_id or "unknown")


@dataclass(frozen=True)
class RetrievalIntent:
    keywords: tuple[str, ...]
    query: str


@dataclass(frozen=True)
class RecallBatch:
    revision: int
    records: tuple[dict, ...]
    all_records: tuple[dict, ...]
    omitted: int


@dataclass(frozen=True)
class Plan:
    operations: tuple[dict, ...]
    uncertain: bool = False


@dataclass(frozen=True)
class SaveResult:
    status: str
    operations: tuple[dict, ...] = ()
    counts: dict | None = None


class JsonModel(Protocol):
    async def ask(self, stage: str, prompt: str, payload: dict, tokens: int) -> dict: ...


class MemoryStore(Protocol):
    async def snapshot(self, role_id: str) -> dict | None: ...
    async def commit(self, payload: dict) -> dict | None: ...
    async def history(self, role_id: str, memory_id: int) -> list | None: ...
