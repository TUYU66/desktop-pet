"""模型传输和诊断；不修复JSON、不重试语义、不加入第三次审查。"""
import asyncio
import json
import re
from .models import ProtocolError


def diagnostic_text(value):
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    return re.sub(r'(?i)((?:api[_-]?key|password|密码|验证码|密钥)\s*[=:：]\s*)[^\s,，;；"}]+', r'\1[REDACTED]', rendered)


class ModelRuntime:
    def __init__(self, llm, config, log):
        self.llm, self.config, self.log = llm, config, log

    async def ask(self, stage, prompt, payload, tokens):
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 16000:
            raise ProtocolError("model_input_budget_exceeded")
        self.log(stage + "_input", payload)
        method = self.llm.response_no_stream
        if self.config.get("structured_output", "auto") != "text" and getattr(self.llm, "supports_json_object", False) is True:
            method = self.llm.response_json
        raw = await asyncio.to_thread(method, prompt, encoded, max_tokens=tokens, temperature=0.1)
        self.log(stage + "_raw_output", raw)
        if not isinstance(raw, str):
            raise ProtocolError("model_response_not_text")
        body = raw.strip()
        if body.startswith("```json\n") and body.endswith("```"):
            body = body[8:-3].strip()
        elif body.startswith("```\n") and body.endswith("```"):
            body = body[4:-3].strip()
        try:
            result = json.loads(body)
        except ValueError:
            self.log(stage + "_protocol_error", {"error": "invalid_json"})
            raise ProtocolError("model_invalid_json") from None
        if not isinstance(result, dict):
            raise ProtocolError("model_envelope_not_object")
        self.log(stage + "_parsed", result)
        return result
