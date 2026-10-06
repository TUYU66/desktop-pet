"""接口失败报告不能泄漏认证信息或响应中的个人数据。"""
import unittest
from unittest.mock import Mock, AsyncMock, patch
import httpx
from config import manage_api_client as api


class MemoryApiDiagnosticsTests(unittest.TestCase):
    def test_status_and_fixed_auth_reason_are_reported_without_secrets(self):
        request=httpx.Request("POST","http://localhost:8000/xiaozhi/api/memories/facts/search",
                              headers={"Service-Key":"secret-test-value"})
        response=httpx.Response(401,request=request,
                                headers={"X-Memory-Auth-Error":"memory_owner_ambiguous"},
                                text="private response body")
        with patch.object(api,"_logger",Mock()) as logger:
            try: response.raise_for_status()
            except httpx.HTTPStatusError as exc: api._memory_failure("读取事实快照",exc)
            message=logger.bind.return_value.warning.call_args.args[0]
        self.assertIn("HTTP 401",message); self.assertIn("没有唯一活跃账户",message)
        self.assertNotIn("secret-test-value",message); self.assertNotIn("private response body",message)

    def test_business_failure_with_http_200_is_not_silent(self):
        response=httpx.Response(200,json={"code":409,"msg":"private detail","data":None})
        with patch.object(api,"_logger",Mock()) as logger:
            self.assertIsNone(api._memory_result("提交事实",response))
            message=logger.bind.return_value.warning.call_args.args[0]
        self.assertIn("409",message); self.assertNotIn("private detail",message)

    def test_success_preserves_snapshot(self):
        response=httpx.Response(200,json={"code":0,"data":{"revision":5}})
        self.assertEqual({"revision":5},api._memory_result("读取事实快照",response))


class ControlledSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_keeps_more_than_twelve_exact_keys_and_query_is_bounded(self):
        client=AsyncMock()
        client.post.return_value=httpx.Response(200,json={"code":0,"data":{"retrievalVersion":1}},
            request=httpx.Request("POST","http://localhost/test"))
        manager=AsyncMock(); manager.__aenter__.return_value=client
        with patch.object(api.httpx,"AsyncClient",return_value=manager):
            result=await api.search_memory_facts("role",keys=[str(i) for i in range(20)],ids=[11],
                controlled_stage="resolve",controlled_query="x"*2500)
        payload=client.post.await_args.kwargs["json"]
        self.assertEqual(20,len(payload["keys"])); self.assertEqual([11],payload["ids"])
        self.assertEqual(2400,len(payload["controlledQuery"])); self.assertEqual(1,result["retrievalVersion"])


if __name__=="__main__": unittest.main()
