"""Chat generation and tool-result coordination, using the owning session state."""

import copy
import json
import uuid
import time
import asyncio
from core.utils.util import extract_json_from_string
from core.reporting.chat_reports import enqueue_tool_report
from core.utils.dialogue import Message, saved_memory_recall_answer
from plugins_func.register import Action, ActionResponse
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
from core.utils.util import get_system_error_response
from core.utils import textUtils
from core.device.chassis_intent import (
    classify_direct_chassis_request,
    parse_chassis_music_request,
    classify_chassis_sequence,
    TURN_TOOL_NAMES,
)

from core.conversation.tool_coordinator import (
    handle_tool_results,
    extract_direct_answer_response,
    clean_response_garbage,
    merge_tool_calls,
    dispatch_tool_calls,
)

TAG = __name__

DIRECT_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "direct_answer",
        "description": "当用户的请求不匹配其他任何工具时，可用此选项直接回复。将回复内容写在response参数里。",
        "parameters": {
            "type": "object",
            "properties": {"response": {"type": "string", "description": "你回复用户的完整内容"}},
            "required": ["response"],
        },
    },
}


class ConversationEngine:
    """Chat behavior shared by the connection; no independent session or locks."""

    def _schedule_memory_save(self):
        """后台提取刚完成的一轮对话，避免待命长连接导致记忆永远不落库。"""
        if self.memory is None or self.loop is None or self.loop.is_closed():
            return
        if getattr(self.memory, "uses_memory_v2", False):
            return  # V2 queues the immutable user turn before generating a reply.
        messages = list(self.dialogue.dialogue)
        last_user_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].role == "user"
                and messages[index].content
                and not messages[index].is_temporary
                and getattr(messages[index], "is_user_input", True)
            ),
            None,
        )
        if last_user_index is None:
            return
        turn_messages = [
            message
            for message in messages[last_user_index:]
            if message.role in ("user", "assistant") and message.content
        ]
        if not any(message.role == "assistant" for message in turn_messages):
            return
        from core.conversation.cancellation import without_chat_cancellation

        with without_chat_cancellation():
            future = asyncio.run_coroutine_threadsafe(
                self.memory.save_memory(
                    turn_messages,
                    self.session_id,
                    user_context=[
                        m
                        for m in messages[:last_user_index]
                        if m.role == "user"
                        and m.content
                        and not m.is_temporary
                        and getattr(m, "is_user_input", True)
                    ][-3:],
                ),
                self.loop,
            )

        def completed(result):
            try:
                result.result()
            except Exception as error:
                self.logger.bind(tag=TAG).error(f"后台保存长期记忆失败: {error}")

        future.add_done_callback(completed)

    def run_web_chat(self, text, request_record=None):
        """Called only after the HTTP handler reserves chat_lock."""
        previous_source = getattr(self, "chat_input_source", None)
        self.chat_input_source = "web"
        try:
            if self.stop_event.is_set():
                return False
            cancellation = getattr(request_record, "cancellation", None)
            if request_record is not None and (
                getattr(self, "web_chat_tracking", None) is not request_record
                or self.session_id != request_record.session
                or request_record.status in ("server_done", "failed", "unknown")
                or (cancellation is not None and cancellation.cancelled())
            ):
                return False
            self.client_abort = False
            from core.reporting.chat_reports import enqueue_asr_report

            enqueue_asr_report(self, text, [])
            from core.conversation.cancellation import chat_scope, ChatTurnCancelled

            try:
                with chat_scope(self, cancellation):
                    return self._chat(text)
            except ChatTurnCancelled:
                return False
        finally:
            self.chat_input_source = previous_source
            self.chat_lock.release()

    def chat(self, query, depth=0, generation=None):
        # Recursive tool follow-ups belong to the already-reserved turn.
        if depth > 0:
            return self._chat(query, depth)
        with self.chat_lock:
            if self.stop_event.is_set() or (
                generation is not None and generation != getattr(self, "input_generation", 0)
            ):
                return False
            # Only the next turn that owns the lock may clear the abort flag.
            # Clearing it at ASR receipt revives the previous model generation.
            self.client_abort = False
            from core.conversation.cancellation import (
                chat_scope,
                ChatTurnCancelled,
                cleanup_cancelled_voice,
            )

            with chat_scope(self) as turn:
                try:
                    result = self._chat(query, depth)
                except ChatTurnCancelled:
                    result = False
                if turn.cancelled() and not self.client_abort:
                    cleanup = asyncio.run_coroutine_threadsafe(
                        cleanup_cancelled_voice(self, turn), self.loop
                    )
                    try:
                        cleanup.result(timeout=3)
                    except Exception:
                        cleanup.cancel()
                        self.logger.bind(tag=TAG).warning("语音超时清理未完成，连接状态待确认")
                return result

    def _chat(self, query, depth=0):
        from core.conversation.cancellation import (
            chat_cancelled,
            wait_chat_future,
            without_chat_cancellation,
        )

        if getattr(self, "client_abort", False) or chat_cancelled():
            return False
        turn_started = time.monotonic()
        from core.music.conversation import split_music_request

        music_request = split_music_request(query) if depth == 0 else None
        action_query = music_request[1] if music_request else query
        if depth == 0:
            from core.conversation.standby import queue_exit_reply

            if queue_exit_reply(self, query):
                return True
            self.task_followup_sentence = None
            from core.reminders.conversation import handle_sync

            if handle_sync(self, action_query):
                return True
        current_sentence_id = None

        if query is not None:
            self.logger.bind(tag=TAG).info(f"大模型收到用户消息: {query}")

        if depth == 0:
            current_sentence_id = str(uuid.uuid4().hex)
            self.sentence_id = current_sentence_id
            self.task_followup_sentence = current_sentence_id
            from core.conversation.requests import bind_sentence

            bind_sentence(self)
            self.pending_chassis_action = None
            self.latest_user_text = query if isinstance(query, str) else ""
            self.dialogue.put(Message(role="user", content=query))
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )
        else:
            current_sentence_id = self.sentence_id

        chassis_music_request = parse_chassis_music_request(action_query) if depth == 0 else None
        chassis_music_action = chassis_music_request[0] if chassis_music_request else None
        chassis_sequence = classify_chassis_sequence(action_query) if depth == 0 else None
        if chassis_sequence:
            self.logger.bind(tag=TAG).info(f"双动作识别成功: {' -> '.join(chassis_sequence)}")
        direct_chassis_action = (
            (chassis_sequence[0] if chassis_sequence else None)
            or chassis_music_action
            or (classify_direct_chassis_request(action_query) if depth == 0 else None)
        )

        MAX_DEPTH = 5
        force_final_answer = False

        if depth >= MAX_DEPTH:
            self.logger.bind(tag=TAG).debug(
                f"已达到最大工具调用深度 {MAX_DEPTH}，将强制基于现有信息回答"
            )
            force_final_answer = True
            self.dialogue.put(
                Message(
                    role="user",
                    content="[系统提示] 已达到最大工具调用次数限制，请你基于目前已经获取的所有信息，直接给出最终答案。不要再尝试调用任何工具。",
                    is_user_input=False,
                )
            )

        functions = None
        if (
            self.intent_type == "function_call"
            and hasattr(self, "func_handler")
            and not force_final_answer
        ):
            # An immediate chassis command can arrive before MCP initialization
            # and the paginated tools/list exchange have both completed.
            chassis_query = direct_chassis_action is not None or (
                depth == 0
                and isinstance(query, str)
                and any(
                    word in query
                    for word in (
                        "起立",
                        "站起来",
                        "起身",
                        "坐下",
                        "坐下来",
                        "躺下",
                        "靠下",
                        "后靠",
                        "休息",
                        "左转",
                        "右转",
                        "向后转",
                        "掉头",
                        "转身",
                        "电量",
                        "电压",
                    )
                )
            )
            if chassis_query:
                deadline = time.monotonic() + 8.0
                mcp_client = getattr(self, "mcp_client", None)
                while (
                    not (mcp_client is not None and mcp_client.ready)
                    and time.monotonic() < deadline
                    and not self.stop_event.is_set()
                ):
                    time.sleep(0.05)
                    mcp_client = getattr(self, "mcp_client", None)
                if mcp_client is not None and mcp_client.ready:
                    self.func_handler.tool_manager.refresh_tools()
                    self.logger.bind(tag=TAG).info("底盘语音请求已等到设备 MCP 工具清单")
                else:
                    self.logger.bind(tag=TAG).warning("等待 8 秒后设备 MCP 工具仍未就绪")
            # Per-request snapshot: never append transient tools to the registry cache.
            functions = copy.deepcopy(self.func_handler.get_functions())
            if functions is not None and depth == 0:
                direct_name = DIRECT_ANSWER_TOOL["function"]["name"]
                if not any(
                    tool.get("function", {}).get("name") == direct_name for tool in functions
                ):
                    functions.append(copy.deepcopy(DIRECT_ANSWER_TOOL))

        if (
            direct_chassis_action
            and self.intent_type == "function_call"
            and hasattr(self, "func_handler")
        ):
            if self.client_abort or chat_cancelled():
                return False
            tool_call = {
                "id": str(uuid.uuid4().hex),
                "name": direct_chassis_action,
                "arguments": "{}",
            }
            enqueue_tool_report(self, direct_chassis_action, {})
            try:
                action_future = asyncio.run_coroutine_threadsafe(
                    self.func_handler.handle_llm_function_call(self, tool_call), self.loop
                )
                result = wait_chat_future(
                    action_future,
                    timeout=(
                        max(int(self.config.get("tool_call_timeout", 30)), 50)
                        if direct_chassis_action in TURN_TOOL_NAMES
                        else int(self.config.get("tool_call_timeout", 30))
                    ),
                )
                enqueue_tool_report(
                    self,
                    direct_chassis_action,
                    {},
                    str(result.result) if result.result else None,
                    report_tool_call=False,
                )
            except Exception as exc:
                if self.client_abort or chat_cancelled():
                    return False
                self.logger.bind(tag=TAG).error(f"身体动作意图处理失败: {exc}")
                result = ActionResponse(
                    action=Action.ERROR, response="我现在没法动，稍后再试好吗？"
                )
            self.logger.bind(tag=TAG).info(f"明确身体动作意图: {direct_chassis_action}")
            if chassis_sequence and self.pending_chassis_action is not None:
                self.pending_chassis_action["next_action"] = chassis_sequence[1]
                labels = {
                    "self_chassis_stand_up": "站起来",
                    "self_chassis_rest": "坐下",
                    "self_chassis_turn_left": "向左转",
                    "self_chassis_turn_right": "向右转",
                    "self_chassis_turn_around": "转身",
                }
                result.response = (
                    f"好，我先{labels[chassis_sequence[0]]}，再{labels[chassis_sequence[1]]}。"
                )
            if chassis_music_action and self.pending_chassis_action is not None:
                self.pending_chassis_action["play_music_after"] = True
                self.pending_chassis_action["music_action"] = chassis_music_request[1]
                current_music = getattr(self, "local_music", None)
                if chassis_music_request[1] == "resume":
                    self.pending_chassis_action["music_target"] = (
                        (current_music.source, current_music.track["id"])
                        if current_music and current_music.track
                        else None
                    )
                result.response = (
                    "好，我先尝试"
                    + ("站起来" if chassis_music_action.endswith("stand_up") else "坐下")
                    + "，确认状态后再"
                    + ("继续播放音乐。" if chassis_music_request[1] == "resume" else "播放音乐。")
                )
            handle_tool_results(self, [(result, tool_call)], depth=0)
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
            self._schedule_memory_save()
            return True

        response_message = []

        try:
            memory_str = None
            memory_feedback = None
            if self.memory is not None:
                prepare_feedback = getattr(self.memory, "prepare_memory_feedback", None)
                if depth == 0 and callable(prepare_feedback):
                    messages = list(self.dialogue.dialogue)
                    if getattr(self.memory, "uses_memory_v2", False):
                        current = copy.deepcopy(messages[-1])
                        operation = self.memory.prepare_chat_memory(current, self.session_id)
                    else:
                        operation = prepare_feedback(
                            messages,
                            self.session_id,
                            user_context=[
                                m
                                for m in messages[:-1]
                                if m.role == "user"
                                and m.content
                                and not m.is_temporary
                                and getattr(m, "is_user_input", True)
                            ][-3:],
                        )
                    # A V2 queue worker may be spawned here. Keep saved user
                    # information independent of the lifetime of its spoken reply.
                    with without_chat_cancellation():
                        future = asyncio.run_coroutine_threadsafe(operation, self.loop)
                    memory_feedback = wait_chat_future(future)
                future = asyncio.run_coroutine_threadsafe(
                    self.memory.query_memory(query or ""), self.loop
                )
                memory_str = wait_chat_future(future)
                if memory_feedback:
                    memory_str = (
                        (memory_str or "")
                        + "\n【本轮记忆操作结果】"
                        + memory_feedback
                        + "\n只能据此说明保存结果；未列出的内容不能声称已保存。继续回答用户本轮其他请求。"
                    )

            if depth == 0:
                self.logger.bind(tag=TAG).info(
                    f"chat_timing memory_prepare_ms={int((time.monotonic()-turn_started)*1000)}"
                )
            # 记忆列表由已提交快照构造，仍走原有 TTS/历史上报链路。
            recall_answer = saved_memory_recall_answer(query, memory_str) if depth == 0 else None
            if memory_feedback and not getattr(self.memory, "uses_memory_v2", False):
                # Non-V2 providers may supply their own already-rendered receipt.
                # Production never imports the retired extraction implementation.
                recall_answer = memory_feedback
            runtime_context = None
            if recall_answer is None:
                from core.device.context import context_for_turn

                runtime_context = context_for_turn(self)
                if self.client_abort:
                    return False
            if recall_answer is not None:
                llm_responses = (
                    iter([(recall_answer, None)])
                    if self.intent_type == "function_call" and functions is not None
                    else iter([recall_answer])
                )
            elif self.intent_type == "function_call" and functions is not None:
                llm_responses = self.llm.response_with_functions(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str,
                        self.config.get("voiceprint", {}),
                        runtime_context=runtime_context,
                    ),
                    functions=functions,
                )
            else:
                llm_responses = self.llm.response(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str,
                        self.config.get("voiceprint", {}),
                        runtime_context=runtime_context,
                    ),
                )
        except Exception as e:
            if self.client_abort or chat_cancelled():
                return False
            self.logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")
            return None

        tool_call_flag = False
        tool_calls_list = []
        content_arguments = ""
        emotion_flag = True
        emotion_prefix = ""
        emotion_default_sent = False
        first_chunk = True
        try:
            for response in llm_responses:
                if first_chunk and depth == 0:
                    self.logger.bind(tag=TAG).info(
                        f"chat_timing first_llm_chunk_ms={int((time.monotonic()-turn_started)*1000)}"
                    )
                    first_chunk = False
                if self.client_abort or chat_cancelled():
                    return False  # Do not execute buffered tools from the interrupted answer.
                if self.intent_type == "function_call" and functions is not None:
                    content, tools_call = response
                    if "content" in response:
                        content = response["content"]
                        tools_call = None
                    if content is not None and len(content) > 0:
                        content_arguments += content

                    if not tool_call_flag and content_arguments.startswith("<tool_call>"):
                        tool_call_flag = True

                    if tools_call is not None and len(tools_call) > 0:
                        tool_call_flag = True
                        merge_tool_calls(tool_calls_list, tools_call)

                    _DA_STREAM_BUFFER = 5
                    for tc in tool_calls_list:
                        if tc["name"] == "direct_answer" and tc.get("arguments"):
                            da_text = extract_direct_answer_response(tc["arguments"])
                            sent_len = tc.get("_da_sent", 0)
                            if da_text and len(da_text) > sent_len:
                                safe_end = max(sent_len, len(da_text) - _DA_STREAM_BUFFER)
                                if safe_end > sent_len:
                                    new_part = da_text[sent_len:safe_end]
                                    new_part = clean_response_garbage(new_part)
                                    if new_part:
                                        tc["_da_sent"] = safe_end
                                        self.tts.tts_text_queue.put(
                                            TTSMessageDTO(
                                                sentence_id=current_sentence_id,
                                                sentence_type=SentenceType.MIDDLE,
                                                content_type=ContentType.TEXT,
                                                content_detail=new_part,
                                            )
                                        )
                else:
                    content = response

                if emotion_flag and content is not None and content.strip():
                    if not emotion_default_sent:
                        asyncio.run_coroutine_threadsafe(textUtils.get_emotion(self, ""), self.loop)
                        emotion_default_sent = True
                    emotion_prefix = (emotion_prefix + content)[:80]
                    # Inspect a bounded prefix, so a split leading emoji is not missed.
                    has_emotion = any(char in textUtils.EMOJI_MAP for char in emotion_prefix)
                    if has_emotion or len(emotion_prefix) >= 80:
                        asyncio.run_coroutine_threadsafe(
                            textUtils.get_emotion(self, emotion_prefix), self.loop
                        )
                        emotion_flag = False

                if content is not None and len(content) > 0:
                    if not tool_call_flag:
                        response_message.append(content)
                        self.tts.tts_text_queue.put(
                            TTSMessageDTO(
                                sentence_id=current_sentence_id,
                                sentence_type=SentenceType.MIDDLE,
                                content_type=ContentType.TEXT,
                                content_detail=content,
                            )
                        )
        except Exception as e:
            if self.client_abort or chat_cancelled():
                return False
            self.logger.bind(tag=TAG).error(f"LLM stream processing error: {e}")
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.MIDDLE,
                    content_type=ContentType.TEXT,
                    content_detail=get_system_error_response(self.config),
                )
            )
            if depth == 0:
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=current_sentence_id,
                        sentence_type=SentenceType.LAST,
                        content_type=ContentType.ACTION,
                    )
                )
            return

        if self.client_abort or chat_cancelled():
            return False
        if tool_call_flag:
            bHasError = False
            if len(tool_calls_list) == 0 and content_arguments:
                a = extract_json_from_string(content_arguments)
                if a is not None:
                    try:
                        content_arguments_json = json.loads(a)
                        tool_calls_list.append(
                            {
                                "id": str(uuid.uuid4().hex),
                                "name": content_arguments_json["name"],
                                "arguments": json.dumps(
                                    content_arguments_json["arguments"], ensure_ascii=False
                                ),
                            }
                        )
                    except Exception as e:
                        bHasError = True
                        response_message.append(a)
                else:
                    bHasError = True
                    response_message.append(content_arguments)
                if bHasError:
                    self.logger.bind(tag=TAG).error(f"function call error: {content_arguments}")

            if not bHasError and len(tool_calls_list) > 0:
                direct_answer_calls = [
                    tc for tc in tool_calls_list if tc["name"] == "direct_answer"
                ]
                real_tool_calls = [tc for tc in tool_calls_list if tc["name"] != "direct_answer"]

                if direct_answer_calls:
                    self.logger.bind(tag=TAG).debug(
                        f"模型选择 direct_answer，流式已播报，写入对话历史"
                    )
                    for tc in direct_answer_calls:
                        da_response = extract_direct_answer_response(tc.get("arguments", "{}"))
                        if da_response:
                            sent_len = tc.get("_da_sent", 0)
                            remaining = da_response[sent_len:]
                            if remaining:
                                remaining = clean_response_garbage(remaining)
                                if remaining:
                                    self.tts.tts_text_queue.put(
                                        TTSMessageDTO(
                                            sentence_id=current_sentence_id,
                                            sentence_type=SentenceType.MIDDLE,
                                            content_type=ContentType.TEXT,
                                            content_detail=remaining,
                                        )
                                    )
                            da_response = clean_response_garbage(da_response)
                            self.tts.store_tts_text(current_sentence_id, da_response)
                            self.dialogue.put(Message(role="assistant", content=da_response))

                    if not real_tool_calls:
                        if depth == 0:
                            self.tts.tts_text_queue.put(
                                TTSMessageDTO(
                                    sentence_id=current_sentence_id,
                                    sentence_type=SentenceType.LAST,
                                    content_type=ContentType.ACTION,
                                )
                            )
                            self._schedule_memory_save()
                        return

                    tool_calls_list = real_tool_calls

            if not bHasError and len(tool_calls_list) > 0:
                self.logger.bind(tag=TAG).debug(f"检测到 {len(tool_calls_list)} 个工具调用")

                streamed_text = ""
                if len(response_message) > 0:
                    streamed_text = "".join(response_message)
                    self.tts.store_tts_text(current_sentence_id, streamed_text)
                    self.dialogue.put(Message(role="assistant", content=streamed_text))
                response_message.clear()

                tool_results = dispatch_tool_calls(self, tool_calls_list)
                if tool_results is None:
                    return False

                if tool_results:
                    if self.client_abort or chat_cancelled():
                        return False
                    handle_tool_results(
                        self, tool_results, depth=depth, streamed_text=streamed_text
                    )

        if self.client_abort or chat_cancelled():
            return False
        if len(response_message) > 0:
            text_buff = "".join(response_message)
            self.tts.store_tts_text(current_sentence_id, text_buff)
            self.dialogue.put(Message(role="assistant", content=text_buff))

        if depth == 0:
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
            self.logger.bind(tag=TAG).debug(
                lambda: json.dumps(self.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False)
            )
            self._schedule_memory_save()

        # 仅在整轮工具链结束后总结，避免拆散工具调用和结果。
        # 摘要失败不丢弃原文，下一轮再重试。
        if depth == 0:
            from core.utils.short_context import summarize_context

            dialogue = self.dialogue
            session_id = self.session_id
            try:
                count = dialogue.compact(
                    lambda previous, older: summarize_context(
                        self.llm, session_id, previous, older
                    ),
                    max_messages=30,
                )
                if count:
                    self.logger.bind(tag=TAG).info(
                        f"短期上下文压缩完成：合并{count}条旧消息，保留最近最多30条及滚动摘要"
                    )
            except Exception as exc:
                self.logger.bind(tag=TAG).warning(
                    f"短期上下文摘要失败，保留原文等待重试：{type(exc).__name__}"
                )

        return True
