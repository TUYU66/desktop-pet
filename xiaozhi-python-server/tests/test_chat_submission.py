"""HTTP routing and session regression tests; no AI model or device required."""
import ast
import asyncio
import json
import unittest
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.api.chat_handler import ChatHandler
from core.utils.dialogue import Dialogue, Message, saved_memory_recall_answer


class ChatSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_schedule_uses_real_user_turn_not_internal_prompt(self):
        source = Path(__file__).resolve().parents[1] / "core" / "conversation" / "engine.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConversationEngine")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_schedule_memory_save")
        namespace = {"asyncio": asyncio, "TAG": "test"}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        dialogue = Dialogue()
        dialogue.put(Message(role="user", content="示例28岁", is_temporary=True))
        dialogue.put(Message(role="user", content="我养了两只猫"))
        actual = Message(role="user", content="我21岁大三了")
        dialogue.put(actual)
        dialogue.put(Message(role="user", content="工具次数已到上限", is_user_input=False))
        dialogue.put(Message(role="assistant", content="好的"))
        memory = SimpleNamespace(save_memory=AsyncMock())
        handler = SimpleNamespace(memory=memory, loop=asyncio.get_running_loop(), dialogue=dialogue, session_id="s", logger=Mock())
        namespace["_schedule_memory_save"](handler)
        await asyncio.sleep(0.02)
        self.assertIs(actual, memory.save_memory.call_args.args[0][0])
        self.assertEqual(["我养了两只猫"], [m.content for m in memory.save_memory.call_args.kwargs["user_context"]])

    async def test_saved_memory_recall_bypasses_llm_but_keeps_tts_and_history(self):
        import queue
        import uuid
        source = Path(__file__).resolve().parents[1] / "core" / "conversation" / "engine.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConversationEngine")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_chat")
        namespace = {"asyncio": asyncio, "uuid": uuid, "json": json, "TAG": "test", "Message": Message,
                     "saved_memory_recall_answer": saved_memory_recall_answer, "TTSMessageDTO": SimpleNamespace,
                     "SentenceType": SimpleNamespace(FIRST="first", MIDDLE="middle", LAST="last"),
                     "ContentType": SimpleNamespace(ACTION="action", TEXT="text"), "DIRECT_ANSWER_TOOL": {},
                     "textUtils": SimpleNamespace(get_emotion=AsyncMock())}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        for intent, query, receipt in (
            ("function_call", "你记住了我什么", None), ("none", "你记住了我什么", None),
            ("function_call", "请记住我养了两只猫", "本次已确认保存：用户养了两只猫"),
            ("none", "请记住我养了两只猫", "本次已确认保存：用户养了两只猫"),
            ("none", "请记住我养了两只猫", "本次未确认成功：用户养了两只猫")):
            handler = SimpleNamespace(client_abort=False, session_id="s", dialogue=Dialogue(), logger=Mock(), config={},
                                      intent_type=intent, memory=SimpleNamespace(query_memory=AsyncMock(return_value="- 用户养了两只猫"),prepare_memory_feedback=AsyncMock(return_value=receipt)),
                                      loop=asyncio.get_running_loop(), llm=Mock(),
                                      func_handler=SimpleNamespace(get_functions=Mock(return_value=[])),
                                      tts=SimpleNamespace(tts_text_queue=queue.Queue(), store_tts_text=Mock()), _schedule_memory_save=Mock())
            handler.dialogue.put(Message(role="system", content="你会偷偷研究男友的编程书。"))
            result = await asyncio.wait_for(asyncio.to_thread(namespace["_chat"], handler, query), 3)
            self.assertTrue(result)
            handler.llm.response.assert_not_called()
            handler.llm.response_with_functions.assert_not_called()
            self.assertIn("用户养了两只猫", handler.dialogue.dialogue[-1].content)
            self.assertNotIn("编程书", handler.dialogue.dialogue[-1].content)
            if receipt: self.assertEqual(receipt,handler.dialogue.dialogue[-1].content)
            queued = list(handler.tts.tts_text_queue.queue)
            self.assertEqual("first", queued[0].sentence_type)
            self.assertEqual("last", queued[-1].sentence_type)
            self.assertIn("用户养了两只猫", queued[1].content_detail)

    def setUp(self):
        with patch("core.api.chat_handler.setup_logging", return_value=Mock()):
            self.api = ChatHandler({}, SimpleNamespace(device_handlers={}))

    async def test_unknown_device_does_not_fall_back_to_another_device(self):
        other = Mock()
        self.api.ws_server.device_handlers["other"] = other
        response = await self.api.handle_send(SimpleNamespace(json=AsyncMock(
            return_value={"text": "hello", "deviceId": "offline"})))
        self.assertEqual(response.status, 404)
        other.executor.submit.assert_not_called()

    async def test_invalid_payloads_return_400(self):
        for body in ([], {"text": None}, {"text": "hi", "sessionId": 42}, {"text": " "}):
            response = await self.api.handle_send(SimpleNamespace(json=AsyncMock(return_value=body)))
            self.assertEqual(response.status, 400)

    async def test_switch_finishes_before_message_is_submitted(self):
        handler = Mock(session_id="old")
        handler.standby = True
        handler.stop_event = threading.Event()
        handler.client_is_speaking = False
        handler.chat_lock = threading.Lock()
        switched = []

        async def switch(session_id):
            await asyncio.sleep(0)
            switched.append(session_id)

        handler.switch_session = AsyncMock(side_effect=switch)
        async def start_playback(conn, text):
            self.assertEqual(switched, ["new"])
            await asyncio.sleep(0)
            switched.append("speaking")
        send_stt = AsyncMock(side_effect=start_playback)
        def submit(*args):
            self.assertFalse(handler.standby)
            self.assertTrue(handler.return_to_standby)
            self.assertEqual(switched, ["new", "speaking"])
            self.assertTrue(handler.client_is_speaking)
            self.assertFalse(handler.client_abort)
            return Mock()
        handler.executor.submit.side_effect = submit
        self.api.ws_server.device_handlers["device"] = handler
        with patch.dict("sys.modules", {"core.handle.sendAudioHandle": SimpleNamespace(send_stt_message=send_stt)}):
            response = await self.api.handle_send(SimpleNamespace(json=AsyncMock(
                return_value={"text": "hello", "sessionId": "new"})))
        self.assertEqual(json.loads(response.text)["code"], 0)
        handler.switch_session.assert_awaited_once_with("new")
        handler.executor.submit.assert_called_once()
        send_stt.assert_awaited_once_with(handler, "hello")

    async def test_submit_failure_restores_listening_and_releases_turn(self):
        handler = Mock(session_id="current")
        handler.stop_event = threading.Event()
        handler.client_is_speaking = False
        handler.chat_lock = threading.Lock()
        handler.websocket = SimpleNamespace(send=AsyncMock())
        handler.executor.submit.side_effect = RuntimeError("executor closed")
        self.api.ws_server.device_handlers["device"] = handler
        with patch.dict("sys.modules", {"core.handle.sendAudioHandle": SimpleNamespace(send_stt_message=AsyncMock())}):
            response = await self.api.handle_send(SimpleNamespace(json=AsyncMock(return_value={"text": "hello"})))
        self.assertEqual(response.status, 500)
        self.assertFalse(handler.chat_lock.locked())
        self.assertFalse(handler.client_is_speaking)
        self.assertEqual(json.loads(handler.websocket.send.call_args.args[0])["state"], "stop")

    async def test_voice_start_helper_sends_start_after_display_text(self):
        # Use the production helper without loading unrelated audio/model dependencies.
        source = Path(__file__).resolve().parents[1] / "core" / "handle" / "sendAudioHandle.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        method = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "send_stt_message")
        async def send_tts(conn, state):
            await conn.websocket.send(json.dumps({"type": "tts", "state": state}))
        namespace = {"json": json, "send_tts_message": send_tts,
                     "textUtils": SimpleNamespace(get_string_no_punctuation_or_emoji=lambda text: text)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        conn = SimpleNamespace(config={}, session_id="s", websocket=SimpleNamespace(send=AsyncMock()), client_is_speaking=False)
        await namespace["send_stt_message"](conn, "hello")
        frames = [json.loads(call.args[0]) for call in conn.websocket.send.call_args_list]
        self.assertEqual([frame["type"] for frame in frames], ["stt", "tts"])
        self.assertEqual(frames[1]["state"], "start")
        self.assertTrue(conn.client_is_speaking)

    async def test_busy_turn_rejects_before_switching_session(self):
        handler = Mock(session_id="old")
        handler.stop_event = threading.Event()
        handler.client_is_speaking = False
        handler.chat_lock = threading.Lock()
        handler.chat_lock.acquire()
        self.api.ws_server.device_handlers["device"] = handler
        response = await self.api.handle_send(SimpleNamespace(json=AsyncMock(
            return_value={"text": "hello", "sessionId": "new"})))
        self.assertEqual(response.status, 409)
        handler.switch_session.assert_not_called()
        handler.executor.submit.assert_not_called()

    async def test_session_switch_keeps_role_memory_and_does_not_resummarize_history(self):
        # Execute the production method in isolation to avoid importing model runtimes.
        source = Path(__file__).resolve().parents[1] / "core" / "transport" / "connection.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConnectionHandler")
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "switch_session")
        namespace = {"asyncio": asyncio, "Dialogue": Dialogue, "TAG": "test"}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        memory = SimpleNamespace(save_memory=AsyncMock(), load_memory_from_db=AsyncMock(),
                                 short_memory="role memory", session_id="old")
        handler = SimpleNamespace(session_id="old", memory=memory,
                                  dialogue=SimpleNamespace(dialogue=[1, 2]), logger=Mock(), prompt="test")
        await asyncio.wait_for(namespace["switch_session"](handler, "new"), 1)
        self.assertEqual(handler.session_id, "new")
        self.assertEqual(memory.session_id, "new")
        self.assertEqual(memory.short_memory, "role memory")
        memory.save_memory.assert_not_awaited()
        memory.load_memory_from_db.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
