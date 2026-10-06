"""确定性执行边界，不推断纠错、归属或自然语言关系。"""
import re

from .models import CATEGORIES, Plan, ProtocolError, integer, text
from .retriever import normalized

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _is_mainly_chinese(value):
    """
    当前用户原话是否主要为中文。

    允许句子中出现STM32、ESP32、Java等英文/型号，
    只判断整体主要语言。
    """
    value = str(value or "")

    cjk_count = len(_CJK_RE.findall(value))
    latin_count = len(_LATIN_RE.findall(value))

    return (
        cjk_count >= 2
        and cjk_count >= latin_count
    )


def _validate_memory_language(memory, turn):
    """
    中文用户输入时，禁止最终长期记忆被整体翻译成英文。

    允许：
        用户使用STM32进行嵌入式开发

    拒绝：
        embedded_development
        I work in embedded development.
    """
    if not _is_mainly_chinese(turn.latest_user):
        return

    for field in ("key", "content"):
        value = str(memory.get(field, ""))

        has_chinese = bool(_CJK_RE.search(value))
        has_latin = bool(_LATIN_RE.search(value))

        if has_latin and not has_chinese:
            raise ProtocolError("memory_language_mismatch")


def memory_fields(raw, turn):
    if (
        not isinstance(raw, dict)
        or raw.get("category") not in CATEGORIES
    ):
        raise ProtocolError("memory_category_invalid")

    memory = {
        "category": raw["category"],
        "key": text(
            raw.get("key"),
            "memory_key_invalid",
            120,
        ),
        "content": text(
            raw.get("content"),
            "memory_content_invalid",
            2000,
        ),
        "source": turn.latest_user,
        "sourceMessageId": turn.message_id,
        "observedAt": turn.observed_at,
    }

    if "time" in raw:
        memory["time"] = text(
            raw["time"],
            "memory_time_invalid",
            120,
        )

    _validate_memory_language(memory, turn)

    return memory


def validate(answer, turn, batch):
    raw_ops = answer.get("operations") if isinstance(answer, dict) else None
    if not isinstance(raw_ops, list) or len(raw_ops) > 8:
        raise ProtocolError("operations_invalid")
    if any(isinstance(op, dict) and op.get("action") == "uncertain" for op in raw_ops):
        return Plan((), uncertain=True)
    visible = {r["id"]: r for r in batch.records}
    actual = {r["id"]: r for r in batch.all_records}
    writes, touched = [], set()
    for raw in raw_ops:
        if not isinstance(raw, dict):
            raise ProtocolError("operation_invalid")
        action = raw.get("action")
        if action == "skip":
            continue
        if action not in {"add", "update"}:
            raise ProtocolError("action_invalid")
        reason = raw.get("reason")
        target = None
        if action == "add":
            if reason != "new" or raw.get("targetId") is not None or raw.get("expectedVersion") is not None:
                raise ProtocolError("add_contract_invalid")
        else:
            if reason not in {"changed", "corrected", "refines", "cancelled", "completed", "invalidated"}:
                raise ProtocolError("update_reason_invalid")
            ident = integer(raw.get("targetId"), "target_id_invalid")
            version = integer(raw.get("expectedVersion"), "target_version_invalid")
            if ident not in visible or ident not in actual or ident in touched:
                raise ProtocolError("target_not_visible_or_repeated")
            target = actual[ident]
            if version != visible[ident]["version"] or version != target["version"]:
                raise ProtocolError("target_version_conflict")
            if target["status"] == "invalidated":
                raise ProtocolError("target_invalidated")
            if target["status"] == "closed" and reason not in {"corrected", "invalidated"}:
                raise ProtocolError("closed_target_immutable")
            if reason in {"cancelled", "completed"} and (target["category"] != "goal" or target["status"] != "active"):
                raise ProtocolError("target_not_active_goal")
            touched.add(ident)
        if reason == "invalidated":
            memory = memory_fields(target, turn)
            explicit = False
        else:
            memory = memory_fields(raw.get("memory"), turn)
            explicit = raw.get("explicit", False)
            if target is not None and memory["category"] != target["category"]:
                raise ProtocolError("update_category_changed")
            if type(explicit) is not bool or memory["category"] == "note" and not explicit:
                raise ProtocolError("note_authorization_required")
            if reason in {"cancelled", "completed"} and memory["category"] != "goal":
                raise ProtocolError("closed_goal_category_invalid")
        write = {"action": "delete" if reason == "invalidated" else action,
                 "reason": reason, "memory": memory, "explicit": explicit}
        if target:
            write.update(targetId=target["id"], expectedVersion=target["version"])
        writes.append(write)
    # 与数据库当前槽的唯一约束一致。此处只拒绝冲突，绝不据key自动选择target。
    projected = {ident: dict(row) for ident, row in actual.items()}
    for offset, op in enumerate(writes):
        old = projected.get(op.get("targetId"))
        status = ("invalidated" if op["reason"] == "invalidated" else "closed" if op["reason"] in {"cancelled", "completed"}
                  else old["status"] if old else "active")
        if op["action"] == "add" and op["memory"]["category"] != "event":
            if any(r["status"] == "active" and r["category"] == op["memory"]["category"]
                   and normalized(r["content"]) == normalized(op["memory"]["content"]) for r in projected.values()):
                raise ProtocolError("duplicate_content")
        projected[op.get("targetId", -offset-1)] = {**op["memory"], "status": status}
    slots = set()
    for row in projected.values():
        if row["status"] != "active" or row["category"] == "event":
            continue
        slot = (row["category"], normalized(row["key"]))
        if slot in slots:
            raise ProtocolError("duplicate_current_topic")
        slots.add(slot)
    return Plan(tuple(writes))
