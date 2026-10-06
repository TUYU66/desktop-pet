"""v4语义记忆生产入口；仅使用category/key/content协议。"""
import asyncio
import json
import os
from collections import deque
from datetime import datetime
from ..base import MemoryProviderBase, logger
from core.utils.dialogue import saved_memory_recall_answer
from .policy import explicit_request, refusal_request
from .facts import redact, redact_tree, digest

TAG=__name__
MAX_PROMPT_CHARS=12000
MAX_ANSWER_CHARS=6000

def format_memories(memories,max_chars=MAX_ANSWER_CHARS):
    lines,used=[],0
    for item in memories or []:
        content=str(item.get("content","")).strip().replace("\n"," ")
        if not content: continue
        if used+len(content)+2+(1 if lines else 0)>max_chars:
            lines.append("【仅展示部分记忆，完整记录请查看网页】"); break
        lines.append("- "+content); used+=len(content)+2+(1 if len(lines)>1 else 0)
    return "\n".join(lines)

class MemoryProvider(MemoryProviderBase):
    def __init__(self,config,summary_memory=None):
        super().__init__(config)
        self.short_memory=""
        self.llm=None
        self._save_lock=asyncio.Lock()
        self._processed=deque(maxlen=256)

    def init_memory(self,role_id,llm,session_id=None,**kwargs):
        super().init_memory(role_id,llm,session_id=session_id,**kwargs)
        self.short_memory=""

    async def _ask_json(self,prompt,data,tokens=1500,diagnostic=None):
        encoded=json.dumps(data,ensure_ascii=False)
        if len(encoded)>MAX_PROMPT_CHARS: raise ValueError("prompt_budget_exceeded")
        trace=data.get("trace")
        if diagnostic:
            self._diagnose(diagnostic+"_input",json.dumps(redact_tree({"trace":trace,"prompt":prompt,"data":data}),ensure_ascii=False))
        responder=self.llm.response_no_stream
        if self.config.get("structured_output","auto")!="text" and getattr(self.llm,"supports_json_object",False) is True:
            responder=self.llm.response_json
        response=await asyncio.to_thread(responder,prompt,encoded,max_tokens=tokens,temperature=0.1)
        if diagnostic:
            # 先记录原始文本，JSON格式错误也能追溯；仅遮蔽敏感值，不截断模型返回。
            self._diagnose(diagnostic+"_raw_output",json.dumps({"trace":trace,"raw":redact(response)},ensure_ascii=False))
        text=response.strip()
        if text.startswith("```"): text=text.split("\n",1)[-1].rsplit("```",1)[0].strip()
        try:
            payload=json.loads(text)
        except (ValueError,TypeError) as exc:
            if diagnostic: self._diagnose(diagnostic+"_parse_error",f"trace={trace}; {type(exc).__name__}")
            raise
        if diagnostic:
            self._diagnose(diagnostic+"_parsed_result",json.dumps(redact_tree({"trace":trace,"parsed":payload}),ensure_ascii=False))
        if not isinstance(payload,dict): raise ValueError("json_envelope_invalid")
        return payload

    def _diagnose(self,phase,reason,fact=None):
        metadata={"phase":phase,"reason":str(reason),"factHash":digest(json.dumps(fact,ensure_ascii=False,sort_keys=True)) if fact else None}
        if os.environ.get("XIAOZHI_MEMORY_DIAGNOSTICS")=="1" and fact:
            metadata["redactedFact"]=redact_tree(fact)
        logger.bind(tag=TAG).info("记忆事实诊断: "+json.dumps(metadata,ensure_ascii=False))

    async def save_memory(self,msgs,session_id=None,user_context=None):
        async with self._save_lock: return await self._save_fact_turn(msgs,session_id,user_context)

    async def prepare_memory_feedback(self,msgs,session_id=None,user_context=None):
        """主动保存先完成事务再反馈；普通自动记忆仍在后台运行。"""
        latest=next((m for m in reversed(msgs or []) if m.role=="user" and m.content
                     and getattr(m,"is_user_input",True) and not getattr(m,"is_temporary",False)),None)
        if latest is None: return None
        text=redact(latest.content)
        if not explicit_request(text) and not refusal_request(text): return None
        async with self._save_lock:
            self._feedback_counts=None
            result=await self._save_fact_turn(msgs,session_id,user_context)
            # 已向用户提供回执的轮次不在回复结束后静默重试，失败由新请求重试。
            turn=getattr(latest,"uniq_id",None)
            if turn and turn not in self._processed: self._processed.append(turn)
            if refusal_request(text):
                return "好，这次的内容不记进长期记忆，之前记下的那些还在。"
            if result is None:
                return "这次有没有记好，我还没确认，过会儿再试吧。"
            counts=self._feedback_counts
            if not result or not counts or not any(counts.get(k,0)>0 for k in ("created","updated","deleted")):
                return "这次长期记忆没有变化，可能之前已经记过，或者这次不适合记下来。你在长期记忆页面看一下吧。"
            lines=[op["memory"]["content"] for op in result if op.get("memory") and op.get("action")!="delete"]
            return "好，这些已经记好了："+"；".join(lines)+"。" if lines else "好，长期记忆改好了。"

    async def _save_fact_turn(self,msgs,session_id,user_context):
        latest=next((m for m in reversed(msgs or []) if m.role=="user" and m.content and not getattr(m,"is_temporary",False) and getattr(m,"is_user_input",True)),None)
        if latest is None or self.llm is None: return None
        reference=getattr(latest,"created_at",None) or datetime.now()
        turn=getattr(latest,"uniq_id",None) or digest(str(session_id)+reference.isoformat()+str(latest.content))
        if turn in self._processed: return []
        text=redact(latest.content)
        if len(text)>2000:
            self._diagnose("extract","latest_user_too_long"); return None
        context=[{"text":redact(m.content)[:350],"timestamp":str(getattr(m,"created_at","unknown")),"messageId":str(getattr(m,"uniq_id",None) or digest(str(m.content)))[:64]} for m in (user_context or [])[-3:] if m.role=="user" and m.content and not getattr(m,"is_temporary",False) and getattr(m,"is_user_input",True)]
        from .semantic import save_turn
        return await save_turn(self,text,reference,turn,session_id,context)

    async def query_memory(self,query):
        from .semantic import recall
        try:
            self.short_memory=await recall(self,query,saved_memory_recall_answer(query,"") is not None)
        except Exception as exc:
            self._diagnose("semantic_recall_failure",type(exc).__name__)
            self.short_memory="长期记忆本轮检索失败；不要编造记忆。"
        return self.short_memory

    async def load_memory_from_db(self,_session_id=None): return await self.query_memory("查看我的长期记忆")
