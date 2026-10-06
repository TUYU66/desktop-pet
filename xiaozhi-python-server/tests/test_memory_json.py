"""结构化模型传输测试；所有网络请求均由替身返回。"""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from core.providers.llm.openai.openai import LLMProvider
from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider
from core.providers.memory.mem_local_short.facts import evidence_matches


class JsonTransportTests(unittest.TestCase):
    def llm(self, reason="stop", content='{"facts":[]}'):
        provider=object.__new__(LLMProvider)
        provider.model_name="synthetic-model"
        provider.base_url="https://open.bigmodel.cn/api/paas/v4/"
        provider.client=NS(chat=NS(completions=NS(create=Mock(return_value=
            NS(choices=[NS(finish_reason=reason,message=NS(content=content))])))))
        return provider

    def test_json_request_is_nonstreaming_and_preserves_prompts(self):
        provider=self.llm()
        self.assertEqual('{"facts":[]}',provider.response_json("system","user",max_tokens=800,temperature=0.1))
        params=provider.client.chat.completions.create.call_args.kwargs
        self.assertFalse(params["stream"]); self.assertEqual({"type":"json_object"},params["response_format"])
        self.assertEqual("user",params["messages"][1]["content"])

    def test_truncated_json_is_rejected_not_completed_locally(self):
        with self.assertRaisesRegex(ValueError,"json_response_truncated"):
            self.llm(reason="length").response_json("system","user")

    def test_empty_json_response_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"json_response_empty"):
            self.llm(content=None).response_json("system","user")

    def test_evidence_cannot_merge_punctuation_into_another_number(self):
        self.assertFalse(evidence_matches("我21岁","我2，1岁"))
        self.assertTrue(evidence_matches("我21岁","我21岁。"))


class MemoryJsonTests(unittest.IsolatedAsyncioTestCase):
    async def test_supported_json_transport_is_preferred(self):
        llm=NS(supports_json_object=True,response_json=Mock(return_value='{"facts":[]}'),response_no_stream=Mock())
        provider=MemoryProvider({}); provider.llm=llm
        self.assertEqual({"facts":[]},await provider._ask_json("system",{}))
        llm.response_json.assert_called_once(); llm.response_no_stream.assert_not_called()

    async def test_unsupported_provider_uses_existing_text_transport(self):
        llm=NS(supports_json_object=False,response_json=Mock(),response_no_stream=Mock(return_value='{"facts":[]}'))
        provider=MemoryProvider({}); provider.llm=llm
        self.assertEqual({"facts":[]},await provider._ask_json("system",{}))
        llm.response_json.assert_not_called()

    async def test_json_api_failure_does_not_silently_retry_in_text_mode(self):
        llm=NS(supports_json_object=True,response_json=Mock(side_effect=RuntimeError("synthetic")),response_no_stream=Mock())
        provider=MemoryProvider({}); provider.llm=llm
        with self.assertRaises(RuntimeError): await provider._ask_json("system",{})
        llm.response_no_stream.assert_not_called()

    async def test_text_compatibility_is_explicitly_configurable(self):
        llm=NS(supports_json_object=True,response_json=Mock(),response_no_stream=Mock(return_value='{"facts":[]}'))
        provider=MemoryProvider({"structured_output":"text"}); provider.llm=llm
        self.assertEqual({"facts":[]},await provider._ask_json("system",{}))
        llm.response_json.assert_not_called()


if __name__=="__main__": unittest.main()
