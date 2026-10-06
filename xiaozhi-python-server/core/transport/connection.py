import copy
import json
import uuid
import time
import queue
import asyncio
import threading
import traceback
import websockets

from typing import Dict, Any
from collections import deque
from core.utils.modules_initialize import initialize_tts, initialize_asr
from core.providers.tts.default import DefaultTTS
from concurrent.futures import ThreadPoolExecutor
from core.utils.dialogue import Message, Dialogue
from core.providers.asr.dto.dto import InterfaceType
from core.handle.textHandle import handleTextMessage
from core.providers.tools.unified_tool_handler import UnifiedToolHandler
from plugins_func.loadplugins import auto_import_modules
from core.transport.auth import AuthenticationError
from config.logger import setup_logging, build_module_string, create_connection_logger
from config.manage_api_client import report_chat_title
from core.utils.prompt_manager import PromptManager
from core.conversation.engine import ConversationEngine

TAG = __name__

auto_import_modules("plugins_func.functions")


class TTSException(RuntimeError):
    pass


class ConnectionHandler(ConversationEngine):
    def __init__(
            self,
            config: Dict[str, Any],
            _vad,
            _asr,
            _llm,
            _memory,
            _intent,
            server=None,
    ):
        self.common_config = config
        self.config = copy.deepcopy(config)
        self.session_id = str(uuid.uuid4())
        self.logger = setup_logging()
        self.server = server

        self.read_config_from_api = False
        self.report_enabled = True

        self.websocket: websockets.ServerConnection | None = None
        self.headers = None
        self.device_id = None
        self.client_ip = None
        self.prompt = None
        self.welcome_msg = None
        self.max_output_size = 0
        self.chat_history_conf = 1
        self.audio_format = "opus"
        self.sample_rate = 24000
        self.server_sample_rate = 24000  # 服务端发送给设备的输出采样率

        self.client_abort = False
        self.client_is_speaking = False
        self.client_listen_mode = "auto"

        self.loop = None
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=5)
        self.chat_lock = threading.Lock()

        # 上报队列（聊天记录 → Java 后端）
        self.report_queue = queue.Queue()
        self.report_thread = None
        self.report_asr_enable = True
        self.report_tts_enable = True
        self.conn_from_mqtt_gateway = False

        # 依赖的组件
        self.vad = None
        self.asr = None
        self.tts = None
        self._asr = _asr
        self._vad = _vad
        self.llm = _llm
        self.memory = _memory
        self.intent = _intent

        # VAD
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_voice_window = deque(maxlen=5)
        self.first_activity_time = 0.0
        self.last_activity_time = 0.0
        self.vad_last_voice_time = 0.0
        self.client_voice_stop = False
        self.last_is_voice = False

        # ASR
        self.asr_audio = []
        self.asr_audio_queue = queue.Queue(maxsize=100)  # At most six seconds at 60 ms/frame.
        self.current_speaker = None

        # LLM
        self.dialogue = Dialogue()

        # TTS
        self.sentence_id = None
        self.tts_MessageText = ""
        self.pending_chassis_action = None
        self.latest_user_text = ""

        # IoT / Tools
        self.iot_descriptors = {}
        self.func_handler = None

        self.cmd_exit = self.config["exit_commands"]
        self.close_after_chat = False
        self.standby = False
        self.return_to_standby = False
        self.conversation_awake = False
        self.standby_after_sentence = None
        self.last_transport_time = time.time() * 1000
        self.load_function_plugin = False
        self.intent_type = "nointent"

        self.timeout_seconds = (
                int(self.config.get("close_connection_no_voice_time", 120)) + 60
        )
        self.timeout_task = None

        self.features = None

        self.prompt_manager = PromptManager(self.config, self.logger)

    async def handle_connection(self, ws: websockets.ServerConnection):
        try:
            self.loop = asyncio.get_running_loop()

            self.headers = dict(ws.request.headers)
            real_ip = self.headers.get("x-real-ip") or self.headers.get("x-forwarded-for")
            if real_ip:
                self.client_ip = real_ip.split(",")[0].strip()
            else:
                self.client_ip = ws.remote_address[0]
            self.logger.bind(tag=TAG).info(
                f"{self.client_ip} conn - Headers: {self.headers}"
            )

            self.device_id = self.headers.get("device-id", None)
            if self.server and self.device_id:
                self.server.device_handlers[self.device_id] = self
                self.logger.bind(tag=TAG).info(f"设备 {self.device_id} 注册到 server 映射表")

            self.websocket = ws

            self.first_activity_time = time.time() * 1000
            self.last_activity_time = time.time() * 1000

            self.timeout_task = asyncio.create_task(self._check_timeout())

            self.welcome_msg = self.config["xiaozhi"]
            self.welcome_msg["session_id"] = self.session_id
            # 从 welcome_msg 获取本设备的输出采样率（硬件扬声器采样率如24000）
            self.server_sample_rate = self.welcome_msg["audio_params"].get("sample_rate", 24000)
            self.sample_rate = self.server_sample_rate
            self.logger.bind(tag=TAG).info(f"服务端输出采样率: {self.server_sample_rate}")

            # 初始化所有组件（同步，本地配置，无需等待 API）
            self._initialize_components()

            try:
                async for message in self.websocket:
                    await self._route_message(message)
            except websockets.exceptions.ConnectionClosed:
                self.logger.bind(tag=TAG).info("客户端断开连接")

        except AuthenticationError as e:
            self.logger.bind(tag=TAG).error(f"Authentication failed: {str(e)}")
            return
        except Exception as e:
            stack_trace = traceback.format_exc()
            self.logger.bind(tag=TAG).error(f"Connection error: {str(e)}-{stack_trace}")
            return
        finally:
            try:
                await self._save_and_close(ws)
            except Exception as final_error:
                self.logger.bind(tag=TAG).error(f"最终清理时出错: {final_error}")
                try:
                    await self.close(ws)
                except Exception as close_error:
                    self.logger.bind(tag=TAG).error(f"强制关闭连接时出错: {close_error}")

    async def _save_and_close(self, ws):
        """完成会话标题上报并关闭连接；长期记忆已在每轮对话后保存。"""
        try:
            java_session_id = self._get_report_session_id()
            if java_session_id:
                def generate_title_task():
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        role_id = self.config.get("activeRole", "")
                        role_name = ""
                        if role_id and self.config.get("roles"):
                            try:
                                import json
                                roles = json.loads(self.config.get("roles", "[]"))
                                for r in roles:
                                    if r.get("id") == role_id:
                                        role_name = r.get("name", "")
                                        break
                            except Exception:
                                pass
                        loop.run_until_complete(
                            report_chat_title(java_session_id, "新会话",
                                               role_id=role_id, role_name=role_name)
                        )
                    except Exception as e:
                        self.logger.bind(tag=TAG).error(f"生成标题失败: {e}")
                    finally:
                        try:
                            loop.close()
                        except Exception:
                            pass

                threading.Thread(target=generate_title_task, daemon=True).start()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"连接收尾失败: {e}")
        finally:
            try:
                await self.close(ws)
            except Exception as close_error:
                self.logger.bind(tag=TAG).error(f"连接收尾后关闭失败: {close_error}")

    async def _route_message(self, message):
        """消息路由"""
        if isinstance(message, str):
            await handleTextMessage(self, message)
        elif isinstance(message, bytes):
            if self.vad is None or self.asr is None:
                return
            try:
                self.asr_audio_queue.put_nowait(message)
            except queue.Full:
                # Do not block WebSocket control/abort messages behind audio backlog.
                while True:
                    try: self.asr_audio_queue.get_nowait()
                    except queue.Empty: break
                self.input_generation = getattr(self, 'input_generation', 0) + 1
                self.reset_audio_states()
                self.logger.bind(tag=TAG).warning('收音队列积压，已丢弃过期片段')

    def _initialize_components(self):
        try:
            if self.tts is None:
                self.tts = self._initialize_tts()
            asyncio.run_coroutine_threadsafe(
                self.tts.open_audio_channels(self), self.loop
            )

            self.selected_module_str = build_module_string(
                self.config.get("selected_module", {})
            )
            self.logger = create_connection_logger(self.selected_module_str)

            if self.config.get("prompt") is not None:
                user_prompt = self.config["prompt"]
                prompt = self.prompt_manager.get_quick_prompt(user_prompt)
                self.change_system_prompt(prompt)
                self.logger.bind(tag=TAG).info(
                    f"快速初始化组件: prompt成功 {prompt[:50]}..."
                )

            if self.vad is None:
                self.vad = self._vad
            if self.asr is None:
                self.asr = self._initialize_asr()

            asyncio.run_coroutine_threadsafe(
                self.asr.open_audio_channels(self), self.loop
            )

            self._initialize_memory()
            self._initialize_intent()
            self._init_report_threads()
            self._init_prompt_enhancement()
            self._inject_tool_call_fewshot()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"实例化组件失败: {e}")

    def _init_prompt_enhancement(self):
        self.prompt_manager.update_context_info(self, self.client_ip)
        enhanced_prompt = self.prompt_manager.build_enhanced_prompt(
            self.config["prompt"], self.device_id, self.client_ip
        )
        if enhanced_prompt:
            self.change_system_prompt(enhanced_prompt)
            self.logger.bind(tag=TAG).debug("系统提示词已增强更新")

    def _inject_tool_call_fewshot(self):
        if self.intent_type != "function_call":
            return
        if not hasattr(self, "func_handler") or self.func_handler is None:
            return

        tools = self.func_handler.get_functions()
        if not tools:
            return

        tool_names = {t.get("function", {}).get("name") for t in tools}

        da_tc_id = "fewshot_da_001"
        self.dialogue.put(Message(role="user", content="给我讲个故事吧", is_temporary=True))
        self.dialogue.put(Message(
            role="assistant",
            tool_calls=[{
                "id": da_tc_id,
                "function": {"arguments": '{"response": "好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一个我给你开讲~"}', "name": "direct_answer"},
                "type": "function", "index": 0,
            }],
            is_temporary=True,
        ))
        self.dialogue.put(Message(
            role="tool", tool_call_id=da_tc_id,
            content="已直接回复", is_temporary=True,
        ))

        if "handle_exit_intent" in tool_names:
            tc_id = "fewshot_exit_001"
            self.dialogue.put(Message(role="user", content="拜拜", is_temporary=True))
            self.dialogue.put(Message(
                role="assistant",
                tool_calls=[{
                    "id": tc_id,
                    "function": {"arguments": '{"say_goodbye": "再见，下次再聊~"}', "name": "handle_exit_intent"},
                    "type": "function", "index": 0,
                }],
                is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="tool", tool_call_id=tc_id,
                content="退出意图已处理", is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="assistant", content="再见，下次再聊~", is_temporary=True,
            ))

        self.logger.bind(tag=TAG).debug("已注入工具调用 few-shot 示例")

    def _init_report_threads(self):
        """初始化聊天记录上报线程"""
        if not self.report_enabled:
            return
        if self.report_thread is None or not self.report_thread.is_alive():
            self.report_thread = threading.Thread(
                target=self._report_worker, daemon=True
            )
            self.report_thread.start()
            self.logger.bind(tag=TAG).info("聊天记录上报线程已启动")

    def _initialize_tts(self):
        tts = initialize_tts(self.config)
        if tts is None:
            tts = DefaultTTS(self.config, delete_audio_file=True)
        # 如果 config 中有运行时音色配置（从 Java 热重载过来的），覆盖默认音色
        runtime_voice = self.config.get("voice")
        if runtime_voice:
            tts.voice = runtime_voice
        return tts

    def _initialize_asr(self):
        if (
                self._asr is not None
                and hasattr(self._asr, "interface_type")
                and self._asr.interface_type == InterfaceType.LOCAL
        ):
            asr = self._asr
        else:
            asr = initialize_asr(self.config)
        return asr

    def _initialize_memory(self):
        if self.memory is None:
            return
        self.memory.init_memory(
            role_id=self.config.get("activeRole", "default"),
            llm=self.llm,
            summary_memory=self.config.get("summaryMemory", None),
            save_to_file=True,
            session_id=self.session_id,
        )

        memory_config = self.config["Memory"]
        memory_type = self.config["Memory"][self.config["selected_module"]["Memory"]]["type"]
        if memory_type == "nomem" or memory_type == "mem_report_only":
            return
        elif memory_type in {"mem_local_short", "memory_v2"}:
            memory_llm_name = memory_config[self.config["selected_module"]["Memory"]]["llm"]
            if memory_llm_name and memory_llm_name in self.config["LLM"]:
                from core.utils import llm as llm_utils
                memory_llm_config = self.config["LLM"][memory_llm_name]
                memory_llm_type = memory_llm_config.get("type", memory_llm_name)
                memory_llm = llm_utils.create_instance(memory_llm_type, memory_llm_config)
                self.logger.bind(tag=TAG).info(f"为长期记忆创建了专用LLM: {memory_llm_name}")
                self.memory.set_llm(memory_llm)
            else:
                self.memory.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM处理长期记忆")

        if getattr(self.memory, "uses_memory_v2", False) and self.loop:
            future = asyncio.run_coroutine_threadsafe(self.memory.initialize_background(), self.loop)
            def initialized(done):
                try:
                    done.result()
                except Exception as error:
                    self.logger.bind(tag=TAG).warning(f"记忆后台初始化失败: {type(error).__name__}")
            future.add_done_callback(initialized)

    def _initialize_intent(self):
        if self.intent is None:
            return
        self.intent_type = self.config["Intent"][
            self.config["selected_module"]["Intent"]
        ]["type"]
        if self.intent_type == "function_call" or self.intent_type == "intent_llm":
            self.load_function_plugin = True

        intent_config = self.config["Intent"]
        intent_type = self.config["Intent"][self.config["selected_module"]["Intent"]]["type"]
        if intent_type == "nointent":
            return
        elif intent_type == "intent_llm":
            intent_llm_name = intent_config[self.config["selected_module"]["Intent"]]["llm"]
            if intent_llm_name and intent_llm_name in self.config["LLM"]:
                from core.utils import llm as llm_utils
                intent_llm_config = self.config["LLM"][intent_llm_name]
                intent_llm_type = intent_llm_config.get("type", intent_llm_name)
                intent_llm = llm_utils.create_instance(intent_llm_type, intent_llm_config)
                self.logger.bind(tag=TAG).info(f"为意图识别创建了专用LLM: {intent_llm_name}")
                self.intent.set_llm(intent_llm)
            else:
                self.intent.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

        self.func_handler = UnifiedToolHandler(self)
        if hasattr(self, "loop") and self.loop:
            asyncio.run_coroutine_threadsafe(self.func_handler._initialize(), self.loop)

    def change_system_prompt(self, prompt):
        from core.conversation.feedback import VOICE_STYLE
        from core.weather.context import WEATHER_USAGE
        if prompt and '[对话表达]' not in prompt:
            prompt += '\n'+VOICE_STYLE
        if prompt and '[天气信息使用规则]' not in prompt:
            prompt += '\n'+WEATHER_USAGE
        self.prompt = prompt
        self.dialogue.update_system_message(self.prompt)

    async def switch_session(self, new_session_id):
        """切换聊天记录会话。长期记忆按角色保存，不随会话切换。"""
        if not new_session_id or new_session_id == self.session_id:
            return
        old_session = self.session_id
        self.input_generation = getattr(self, 'input_generation', 0) + 1
        self.logger.bind(tag=TAG).info(f"切换会话: {old_session} → {new_session_id}")
        self.dialogue = Dialogue()
        self.session_id = new_session_id
        if self.prompt:
            self.dialogue.update_system_message(self.prompt)
        if self.memory:
            self.memory.session_id = new_session_id

        self.logger.bind(tag=TAG).info(f"会话已切换到 {new_session_id}")


    def _report_worker(self):
        """聊天记录上报工作线程"""
        while not self.stop_event.is_set():
            try:
                item = self.report_queue.get(timeout=1)
                if item is None:
                    break
                try:
                    if self.executor is None:
                        continue
                    self.executor.submit(self._process_report, *item)
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"聊天记录上报线程异常: {e}")
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.bind(tag=TAG).error(f"聊天记录上报工作线程异常: {e}")

        self.logger.bind(tag=TAG).info("聊天记录上报线程已退出")

    def _get_report_session_id(self):
        """获取 Java 端的角色 session ID，确保前后端看到同一条聊天记录"""
        try:
            role_id = self.config.get("activeRole", "default")
            role_name = ""
            roles_raw = self.config.get("roles", "[]")
            try:
                roles = json.loads(roles_raw) if isinstance(roles_raw, str) else roles_raw if isinstance(roles_raw, list) else []
                for r in roles:
                    if r.get("id") == role_id:
                        role_name = r.get("name", role_name)
                        break
            except Exception:
                pass
            import httpx
            resp = httpx.get(
                f"http://localhost:8000/xiaozhi/api/chat/session-by-role/{role_id}",
                params={"roleName": role_name},
                headers={"Service-Key": "xiaozhi-python"},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                if data and data.get("sessionId"):
                    return data["sessionId"]
        except Exception as e:
            self.logger.bind(tag=TAG).debug(f"获取Java session失败，使用本地ID: {e}")
        return self.session_id

    def _process_report(self, type, text, audio_data, report_time):
        """处理上报任务（同步HTTP POST，不依赖事件循环）"""
        try:
            import httpx
            session_id = self._get_report_session_id()
            url = "http://localhost:8000/xiaozhi/api/chat/report"
            payload = {
                "sessionId": session_id,
                "chatType": str(type),
                "content": text,
                "macAddress": self.device_id or "unknown",
                "reportTime": report_time,
            }
            resp = httpx.post(url, json=payload, headers={"Service-Key": "xiaozhi-python"}, timeout=10)
            if resp.status_code != 200:
                self.logger.bind(tag=TAG).error(f"上报失败: HTTP {resp.status_code}")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"上报处理异常: {e}")

    def clearSpeakStatus(self):
        self.client_is_speaking = False
        self.last_conversation_activity = time.monotonic()
        self.logger.bind(tag=TAG).debug(f"清除服务端讲话状态")

    async def close(self, ws=None):
        """资源清理方法"""
        try:
            from core.conversation.cancellation import cancel_active_chat
            cancel_active_chat(self)
            self.input_generation = getattr(self, 'input_generation', 0) + 1
            current_task = asyncio.current_task()
            for name in ('_asr_worker', '_standby_retry_task', '_standby_reply_task', '_config_apply_task',
                         'task_followup_wake_task', 'task_followup_audio_task',
                         'task_followup_task', 'reminder_delivery_task'):
                task = getattr(self, name, None)
                if task and task is not current_task and not task.done(): task.cancel()
            local_music = getattr(self, "local_music", None)
            if local_music:
                await local_music.close()
            if hasattr(self, "vad") and self.vad and hasattr(self.vad, "release_conn_resources"):
                self.vad.release_conn_resources(self)

            if hasattr(self, "audio_buffer"):
                self.audio_buffer.clear()

            if self.timeout_task and not self.timeout_task.done():
                self.timeout_task.cancel()
                try:
                    await self.timeout_task
                except asyncio.CancelledError:
                    pass
                self.timeout_task = None

            if hasattr(self, "func_handler") and self.func_handler:
                try:
                    await self.func_handler.cleanup()
                except Exception as cleanup_error:
                    self.logger.bind(tag=TAG).error(f"清理工具处理器时出错: {cleanup_error}")

            if self.stop_event:
                pass

            self.clear_queues()

            try:
                if ws:
                    try:
                        if hasattr(ws, "closed") and not ws.closed:
                            await ws.close()
                        elif hasattr(ws, "state") and ws.state.name != "CLOSED":
                            await ws.close()
                        else:
                            await ws.close()
                    except Exception:
                        pass
                elif self.websocket:
                    try:
                        if hasattr(self.websocket, "closed") and not self.websocket.closed:
                            await self.websocket.close()
                        elif hasattr(self.websocket, "state") and self.websocket.state.name != "CLOSED":
                            await self.websocket.close()
                        else:
                            await self.websocket.close()
                    except Exception:
                        pass
            except Exception as ws_error:
                self.logger.bind(tag=TAG).error(f"关闭WebSocket连接时出错: {ws_error}")

            if self.tts:
                await self.tts.close()
            if self.asr:
                await self.asr.close()

            if self.executor:
                try:
                    self.executor.shutdown(wait=False)
                except Exception as executor_error:
                    self.logger.bind(tag=TAG).error(f"关闭线程池时出错: {executor_error}")
                self.executor = None
            self.logger.bind(tag=TAG).info("连接资源已释放")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"关闭连接时出错: {e}")
        finally:
            if self.server and self.device_id:
                if self.server.device_handlers.get(self.device_id) is self:
                    self.server.device_handlers.pop(self.device_id, None)
                self.logger.bind(tag=TAG).info(f"设备 {self.device_id} 已从 server 映射表注销")
            if self.stop_event:
                self.stop_event.set()

    def clear_queues(self):
        """清空所有任务队列（保留上报队列，防止未提交的聊天记录丢失）"""
        if self.tts:
            self.logger.bind(tag=TAG).debug(
                f"开始清理: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

            for q in [self.tts.tts_text_queue, self.tts.tts_audio_queue]:
                if not q:
                    continue
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break

            if hasattr(self, "audio_rate_controller") and self.audio_rate_controller:
                self.audio_rate_controller.reset()
                self.logger.bind(tag=TAG).debug("已重置音频流控器")

            self.logger.bind(tag=TAG).debug(
                f"清理结束: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

    def reset_audio_states(self):
        self.client_audio_buffer.clear()
        self.client_have_voice = False
        self.client_voice_stop = False
        self.client_voice_window.clear()
        self.last_is_voice = False
        self.vad_last_voice_time = 0.0
        self.asr_audio.clear()
        # 同时重置 VAD 模型内部状态，确保新一轮语音检测从干净起点开始
        if self.vad is not None and hasattr(self.vad, 'reset_vad_state'):
            self.vad.reset_vad_state(self)
        self.logger.bind(tag=TAG).debug("All audio states reset.")


    async def _check_timeout(self):
        try:
            while not self.stop_event.is_set():
                last_activity_time = self.last_activity_time
                from core.conversation.standby import supports_standby, maybe_idle_standby
                if supports_standby(self):
                    now = time.time() * 1000
                    if now - self.last_transport_time > self.timeout_seconds * 1000:
                        await self.close(self.websocket)
                        break
                    await maybe_idle_standby(self)
                    await asyncio.sleep(2)
                    continue
                if last_activity_time > 0.0:
                    current_time = time.time() * 1000
                    if current_time - last_activity_time > self.timeout_seconds * 1000:
                        if not self.stop_event.is_set():
                            self.logger.bind(tag=TAG).info("连接超时，准备关闭")
                            self.stop_event.set()
                            try:
                                await self.close(self.websocket)
                            except Exception as close_error:
                                self.logger.bind(tag=TAG).error(f"超时关闭连接时出错: {close_error}")
                        break
                await asyncio.sleep(10)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"超时检查任务出错: {e}")
        finally:
            self.logger.bind(tag=TAG).info("超时检查任务已退出")


