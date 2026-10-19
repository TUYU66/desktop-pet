"""Tool stream assembly and result coordination, independent of transport handlers."""

import asyncio
import json
import re
import uuid
from typing import Any, Mapping, Protocol
from core.conversation.cancellation import chat_cancelled, wait_chat_future
from core.device.chassis_intent import TURN_TOOL_NAMES
from core.reporting.chat_reports import enqueue_tool_report
from core.utils.dialogue import Message
from core.providers.tts.dto.dto import ContentType
from plugins_func.register import Action, ActionResponse

TAG = __name__


class ToolSession(Protocol):
    """Only the state and callbacks used by tool coordination; no transport methods."""

    client_abort: bool
    config: Mapping[str, Any]
    logger: Any
    loop: asyncio.AbstractEventLoop
    func_handler: Any
    dialogue: Any
    tts: Any
    sentence_id: str

    def chat(self, query: str | None, depth: int = 0) -> Any: ...


def handle_tool_results(session: ToolSession, tool_results, depth, streamed_text=""):
    need_llm_tools = []
    record_tools = []

    for result, tool_call_data in tool_results:
        tool_call_data = {**tool_call_data, "id": tool_call_data.get("id") or str(uuid.uuid4())}
        if session.client_abort or chat_cancelled():
            return
        if result.action in [Action.RESPONSE, Action.NOTFOUND, Action.ERROR]:
            text = result.response if result.response else result.result
            if streamed_text and text in streamed_text:
                session.logger.bind(tag=TAG).debug(
                    f"Skipping duplicate TTS for tool {tool_call_data.get('name', 'unknown')}, already streamed"
                )
            else:
                session.tts.tts_one_sentence(session, ContentType.TEXT, content_detail=text)
                session.tts.store_tts_text(session.sentence_id, text)
            session.dialogue.put(Message(role="assistant", content=text))
        elif result.action == Action.REQLLM:
            need_llm_tools.append((result, tool_call_data))
        elif result.action == Action.RECORD:
            record_tools.append((result, tool_call_data))
        else:
            pass

    if record_tools:
        all_tool_calls = [
            {
                "id": tool_call_data["id"],
                "function": {
                    "arguments": (
                        "{}" if tool_call_data["arguments"] == "" else tool_call_data["arguments"]
                    ),
                    "name": tool_call_data["name"],
                },
                "type": "function",
                "index": idx,
            }
            for idx, (_, tool_call_data) in enumerate(record_tools)
        ]
        session.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

        for result, tool_call_data in record_tools:
            text = result.result or ""
            session.dialogue.put(
                Message(
                    role="tool",
                    tool_call_id=(
                        str(uuid.uuid4()) if tool_call_data["id"] is None else tool_call_data["id"]
                    ),
                    content=text,
                )
            )

        response_parts = []
        for result, _ in record_tools:
            resp = result.response or result.result
            if resp:
                response_parts.append(resp)
        if response_parts:
            session.dialogue.put(Message(role="assistant", content="，".join(response_parts)))

    if need_llm_tools:
        all_tool_calls = [
            {
                "id": tool_call_data["id"],
                "function": {
                    "arguments": (
                        "{}" if tool_call_data["arguments"] == "" else tool_call_data["arguments"]
                    ),
                    "name": tool_call_data["name"],
                },
                "type": "function",
                "index": idx,
            }
            for idx, (_, tool_call_data) in enumerate(need_llm_tools)
        ]
        session.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

        for result, tool_call_data in need_llm_tools:
            # Every tool call needs a matching result, including an empty result.
            session.dialogue.put(
                Message(
                    role="tool",
                    tool_call_id=tool_call_data["id"],
                    content="" if result.result is None else str(result.result),
                )
            )

        session.chat(None, depth=depth + 1)


def extract_direct_answer_response(arguments_str):
    if not isinstance(arguments_str, str) or not arguments_str:
        return ""
    try:
        data = json.loads(arguments_str)
        if isinstance(data, dict) and "response" in data:
            return data["response"] if isinstance(data["response"], str) else ""
    except (json.JSONDecodeError, TypeError):
        pass
    marker = '"response": "'
    idx = arguments_str.find(marker)
    if idx < 0:
        marker = '"response":"'
        idx = arguments_str.find(marker)
    if idx < 0:
        return ""
    start = idx + len(marker)
    raw = arguments_str[start:]
    if raw.endswith('"}'):
        raw = raw[:-2]
    elif raw.endswith('"'):
        raw = raw[:-1]
    raw = raw.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
    return raw


def clean_response_garbage(text):
    if not text:
        return text
    _garbage_chars = frozenset("\")'}）")
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) <= 8 and all(c in _garbage_chars for c in stripped):
            continue
        cleaned.append(line)
    result = "\n".join(cleaned)
    result = re.sub(r'["\'}\]]+$', "", result.rstrip()).rstrip()
    return result


def merge_tool_calls(tool_calls_list, tools_call):
    for tool_call in tools_call:
        tool_index = getattr(tool_call, "index", None)
        if tool_index is None:
            tool_index = (
                len(tool_calls_list)
                if tool_call.function.name
                else max(0, len(tool_calls_list) - 1)
            )
        if type(tool_index) is not int or not 0 <= tool_index < 32:
            raise ValueError("Invalid tool stream index")
        while tool_index >= len(tool_calls_list):
            tool_calls_list.append({"id": "", "name": "", "arguments": ""})
        target = tool_calls_list[tool_index]
        if tool_call.id:
            if target["id"] and target["id"] != tool_call.id:
                raise ValueError("Conflicting tool stream identity")
            target["id"] = tool_call.id
        if tool_call.function.name:
            target["name"] = tool_call.function.name
        if tool_call.function.arguments:
            target["arguments"] += tool_call.function.arguments


def dispatch_tool_calls(session: ToolSession, tool_calls):
    """Validate the entire batch before dispatch; never replay an uncertain action."""
    prepared = []
    identities = set()
    rejection = "这次操作参数有误，我没有执行。"
    try:
        for call in tool_calls:
            if (
                not isinstance(call.get("name"), str)
                or not call["name"]
                or not isinstance(call.get("id"), str)
                or not call["id"]
            ):
                rejection = "这次操作缺少必要的名称或编号，我没有执行。"
                raise ValueError("Incomplete tool identity")
            if call["id"] in identities:
                rejection = "这批操作的编号重复，我没有执行其中任何一项。"
                raise ValueError("Duplicate tool identity")
            identities.add(call["id"])
            arguments = json.loads(call.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            prepared.append((dict(call), arguments))
    except (ValueError, TypeError):
        session.logger.bind(tag=TAG).warning(rejection)
        return [(ActionResponse(action=Action.ERROR, result=rejection), {})]

    try:
        base_timeout = max(1, int(session.config.get("tool_call_timeout", 30)))
    except (TypeError, ValueError):
        base_timeout = 30
    pending = []
    for call, arguments in prepared:
        if session.client_abort or chat_cancelled():
            return None
        enqueue_tool_report(session, call["name"], arguments)
        future = asyncio.run_coroutine_threadsafe(
            session.func_handler.handle_llm_function_call(session, call), session.loop
        )
        pending.append((future, call, arguments))

    results = []
    for future, call, arguments in pending:
        try:
            timeout = max(base_timeout, 50) if call["name"] in TURN_TOOL_NAMES else base_timeout
            result = wait_chat_future(future, timeout=timeout)
            results.append((result, call))
            enqueue_tool_report(
                session,
                call["name"],
                arguments,
                str(result.result) if result.result else None,
                report_tool_call=False,
            )
        except Exception as error:
            if session.client_abort or chat_cancelled():
                return None
            session.logger.bind(tag=TAG).error(
                f"工具结果未确认: {call['name']}, {type(error).__name__}"
            )
            results.append(
                (
                    ActionResponse(
                        action=Action.ERROR,
                        result="这次操作结果还没确认，先看看设备的状态，别连续重试。",
                    ),
                    call,
                )
            )
            enqueue_tool_report(
                session, call["name"], arguments, str(error), report_tool_call=False
            )
    return results
