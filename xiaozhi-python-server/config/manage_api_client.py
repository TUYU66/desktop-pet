"""简化的聊天记录上报客户端 — 直接 HTTP POST 到 Java 后端"""

import json
import os
import httpx
from typing import Optional, List
from config.logger import setup_logging

TAG = __name__
_logger = setup_logging()

_COMMON_HEADERS = {"Service-Key": "xiaozhi-python"}

_MEMORY_FAILURE_HINTS = {
    "memory_service_key_invalid": "Java/Python 记忆服务密钥不一致",
    "memory_owner_ambiguous": "没有唯一活跃账户；请配置私有服务密钥和明确的记忆所属账户",
    "memory_owner_requires_private_key": "指定记忆所属账户必须使用私有服务密钥",
    "memory_owner_invalid": "记忆所属账户不存在、未启用或ID格式无效",
}


def _memory_failure(operation, exc):
    """只记录协议状态，不把响应正文、认证头或记忆数据写入日志。"""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        reason = exc.response.headers.get("X-Memory-Auth-Error", "")
        hint = _MEMORY_FAILURE_HINTS.get(reason)
        if hint is None:
            hint = {401: "请核对 Java 是否已重启，以及两端的私有服务密钥/所属账户配置",
                    404: "请确认 Java 已加载新版事实接口，且 JAVA_SERVER_URL 指向正确服务",
                    409: "快照版本已变化，请重新读取",
                    500: "请查看 Java 后端异常和数据库迁移状态"}.get(status, "请检查 Java 接口状态")
        detail = f"HTTP {status}; {hint}"
    else:
        detail = type(exc).__name__
    _logger.bind(tag=TAG).warning(f"{operation}失败: {detail}")


def _memory_result(operation, response):
    result = response.json()
    if not isinstance(result, dict) or result.get("code") != 0:
        code = result.get("code") if isinstance(result, dict) else "invalid_envelope"
        # Java 业务错误可能使用 HTTP 200，仍须明确报告而非静默返回 None。
        # 仅记录服务端固定错误的代码，不把任意响应正文/凭据写入日志。
        reasons = {"记忆已变化，请重新读取":"revision_conflict", "分页期间记忆已变化":"snapshot_revision_conflict",
                   "目标不存在、重复或版本变化":"target_version_conflict", "同主题已有当前记忆":"current_topic_conflict",
                   "记忆格式不符合当前协议，请清空旧数据后重新测试":"schema_mismatch",
                   "普通更新必须复用目标主题和类别":"target_topic_mismatch"}
        reason = reasons.get(result.get("msg"), "unclassified") if isinstance(result,dict) else "invalid_envelope"
        _logger.bind(tag=TAG).warning(f"{operation}失败: API code={code}; reason={reason}")
        return None
    return result.get("data")


def memory_headers():
    headers = {"Service-Key": os.environ.get("XIAOZHI_MEMORY_SERVICE_KEY", "xiaozhi-python")}
    owner = os.environ.get("XIAOZHI_MEMORY_OWNER_ID")
    if owner:
        headers["Memory-Owner-Id"] = owner
    return headers


async def search_memory_facts(role_id, keys=(), terms=(), recall=False, include_profile=False, controlled_stage=None, ids=(), controlled_query=""):
    """v2 有界、带版本的快照；不降级到无版本写入协议。"""
    payload = {"roleId": role_id, "keys": list(keys)[:12], "terms": list(terms)[:12], "recall": recall, "includeProfile": include_profile}
    if controlled_stage is not None:
        payload.update(controlledStage=controlled_stage,keys=list(keys)[:32],ids=list(ids)[:8],controlledQuery=controlled_query[:2400])
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(_java_base_url()+"/xiaozhi/api/memories/facts/search", json=payload, headers=memory_headers())
            response.raise_for_status()
            return _memory_result("读取事实快照", response)
    except Exception as exc:
        _memory_failure("读取事实快照", exc)
        return None


async def commit_memory_facts(role_id, session_id, turn_id, revision, operations):
    payload = {"roleId": role_id, "sessionId": session_id or "unknown", "turnId": turn_id,
               "expectedRevision": revision, "operations": operations,"writeProtocolVersion":3}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(_java_base_url()+"/xiaozhi/api/memories/facts/commit", json=payload, headers=memory_headers())
            response.raise_for_status()
            return _memory_result("提交事实", response)
    except Exception as exc:
        _memory_failure("提交事实", exc)
        return None


class SemanticMemoryConflict(RuntimeError):
    pass


async def semantic_memory_request(operation, payload, *, strict=False):
    """v4语义协议；失败不回退到旧事实写入。"""
    if operation not in {"search", "commit"}:
        raise ValueError("semantic_operation_invalid")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(_java_base_url()+"/xiaozhi/api/memories/semantic/"+operation,
                                         json=payload, headers=memory_headers())
            if strict and response.status_code == 409:
                raise SemanticMemoryConflict("commit_conflict")
            response.raise_for_status()
            if strict:
                envelope = response.json()
                if isinstance(envelope, dict) and envelope.get("code") == 409:
                    raise SemanticMemoryConflict("commit_conflict")
            return _memory_result("语义记忆"+operation, response)
    except SemanticMemoryConflict:
        raise
    except Exception as exc:
        _memory_failure("语义记忆"+operation, exc)
        if strict:
            raise
        return None


async def semantic_memory_snapshot(role_id):
    """完整分页快照；版本变化或超出预算时失败，不能把未查到当不存在。"""
    rows, revision, after = [], None, 0
    for _ in range(40):
        payload = {"roleId": role_id, "afterId": after}
        if revision is not None:
            payload["expectedRevision"] = revision
        page = await semantic_memory_request("search", payload)
        if not isinstance(page, dict) or page.get("schemaVersion") != 3 or page.get("writeProtocolVersion") != 4 or page.get("roleId") != role_id:
            return None
        if revision is not None and page.get("revision") != revision:
            return None
        revision = page.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or not isinstance(page.get("memories"), list):
            return None
        rows.extend(page["memories"])
        if page.get("hasMore") is False:
            return {"revision": revision, "memories": rows}
        cursor = page.get("nextId")
        if not isinstance(cursor, int) or cursor <= after:
            return None
        after = cursor
    _logger.bind(tag=TAG).warning("语义记忆快照超过2000条预算；本轮不写入")
    return None


async def semantic_memory_history(role_id, memory_id):
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(_java_base_url()+f"/xiaozhi/api/memories/semantic/{int(memory_id)}/history",
                                        params={"roleId": role_id}, headers=memory_headers())
            response.raise_for_status()
            return _memory_result("语义记忆历史", response)
    except Exception as exc:
        _memory_failure("语义记忆历史", exc)
        return None


def _java_base_url() -> str:
    return os.environ.get("JAVA_SERVER_URL", "http://localhost:8000").rstrip("/")


async def report(
    mac_address: str,
    session_id: str,
    chat_type: int,
    content: str,
    audio,
    report_time,
    device_id: str = None,
    java_base_url: str = "http://localhost:8000",
) -> Optional[dict]:
    """上报一条聊天记录到 Java 后端"""
    if not content:
        return None
    url = f"{java_base_url}/xiaozhi/api/chat/report"
    payload = {
        "sessionId": session_id,
        "chatType": str(chat_type),
        "content": content,
        "macAddress": mac_address,
        "reportTime": report_time,
    }
    if device_id:
        payload["deviceId"] = device_id
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=_COMMON_HEADERS)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        _logger.bind(tag=TAG).error(f"上报失败: {e}")
        return None


async def report_chat_title(
    session_id: str,
    title: str,
    java_base_url: str = "http://localhost:8000",
    role_id: str = None,
    role_name: str = None,
) -> Optional[dict]:
    """上报自动生成的会话标题到 Java 后端"""
    if not session_id or not title:
        return None
    url = f"{java_base_url}/xiaozhi/api/chat/title/generate"
    payload = {"sessionId": session_id, "title": title}
    if role_id:
        payload["roleId"] = role_id
    if role_name:
        payload["roleName"] = role_name
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=_COMMON_HEADERS)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        _logger.bind(tag=TAG).error(f"标题上报失败: {e}")
        return None


async def save_memory_summary(
    session_id: str,
    summary: str,
    java_base_url: str = "http://localhost:8000",
) -> Optional[dict]:
    """保存记忆摘要到 Java 数据库"""
    if not session_id or not summary:
        return None
    url = f"{java_base_url}/xiaozhi/api/chat/memory/{session_id}"
    payload = {"summary": summary}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=_COMMON_HEADERS)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        _logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
        return None


async def load_memory_summary(
    session_id: str,
    java_base_url: str = "http://localhost:8000",
) -> Optional[str]:
    """从 Java 数据库加载记忆摘要"""
    if not session_id:
        return None
    url = f"{java_base_url}/xiaozhi/api/chat/sessions/{session_id}/memory"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, headers=_COMMON_HEADERS)
            if resp.status_code == 200:
                data = resp.json().get("data")
                return data if data else ""
            return ""
    except Exception as e:
        _logger.bind(tag=TAG).error(f"加载记忆失败: {e}")
        return None


async def load_role_memories(role_id: str) -> Optional[List[dict]]:
    """读取当前用户、当前角色的长期记忆。连接失败返回 None，成功但无记录返回空列表。"""
    role_id = (role_id or "default").strip() or "default"
    url = f"{_java_base_url()}/xiaozhi/api/memories"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, params={"roleId": role_id}, headers=_COMMON_HEADERS)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("code") != 0:
                return None
            data = payload.get("data", [])
            return data if isinstance(data, list) else []
    except Exception as e:
        _logger.bind(tag=TAG).error(f"加载长期记忆失败: {e}")
        return None


async def import_role_memories(
    role_id: str, session_id: str, memories: List[dict]
) -> Optional[dict]:
    """将一轮对话中新提取的长期信息导入 Java 数据库。"""
    if not memories:
        return {"created": 0}
    url = f"{_java_base_url()}/xiaozhi/api/memories/import"
    payload = {
        "roleId": (role_id or "default").strip() or "default",
        "sessionId": session_id or "",
        "memories": memories,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=_COMMON_HEADERS)
            resp.raise_for_status()
            result = resp.json()
            return result.get("data") if result.get("code") == 0 else None
    except Exception as e:
        _logger.bind(tag=TAG).error(f"导入长期记忆失败: {e}")
        return None


async def apply_memory_operations(
    role_id: str, session_id: str, operations: List[dict]
) -> Optional[dict]:
    """提交本轮记忆新增、更新和删除操作，由 Java 再校验用户及记录归属。"""
    if not operations:
        return {"created": 0, "updated": 0, "deleted": 0}
    url = f"{_java_base_url()}/xiaozhi/api/memories/operations"
    payload = {
        "roleId": (role_id or "default").strip() or "default",
        "sessionId": session_id or "",
        "operations": operations,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=_COMMON_HEADERS)
            resp.raise_for_status()
            result = resp.json()
            return result.get("data") if result.get("code") == 0 else None
    except Exception as e:
        _logger.bind(tag=TAG).error(f"应用长期记忆变更失败: {e}")
        return None


async def semantic_memory_revision(role_id):
    try:
        async with httpx.AsyncClient(timeout=1) as client:
            response = await client.get(_java_base_url() + "/xiaozhi/api/memories/semantic/revision",
                                        params={"roleId": role_id}, headers=memory_headers())
            response.raise_for_status()
            data = _memory_result("记忆版本", response)
            value = data.get("revision") if isinstance(data, dict) else None
            return value if type(value) is int and value >= 0 else None
    except Exception:
        return None  # Older server: fall back to a verified full snapshot.
