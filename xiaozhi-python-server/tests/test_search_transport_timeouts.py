import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx
from core.web_search.transport import post_search
from core.web_search.provider import search_evidence


class SearchTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def invoke_curl(self, code, output):
        process = NS(returncode=code, communicate=AsyncMock(return_value=(output, b'')))
        with patch('core.web_search.transport.Path.is_file', return_value=True), \
             patch('core.web_search.transport.shutil.which', return_value='curl'), \
             patch('core.web_search.transport.asyncio.create_subprocess_exec', new_callable=AsyncMock,
                   return_value=process) as spawn:
            response = await post_search('curl', 'test-key', {'query': 'test'}, 24, 6)
        self.assertEqual(1, spawn.await_count)
        args = spawn.call_args.args
        self.assertNotIn('test-key', ' '.join(args))
        self.assertEqual('24.0', args[args.index('--max-time') + 1])
        self.assertEqual('6.0', args[args.index('--connect-timeout') + 1])
        return response

    async def test_one_request_preserves_body_and_http_status(self):
        response = await self.invoke_curl(0, b'{"results": []}\n200 0.24')
        self.assertEqual(200, response.status_code)
        self.assertEqual({'results': []}, response.json())

    async def test_connection_timeout_is_distinct_from_search_read_timeout(self):
        with self.assertRaises(httpx.ConnectTimeout):
            await self.invoke_curl(28, b'\n000 0.000000')
        with self.assertRaises(httpx.ReadTimeout):
            await self.invoke_curl(28, b'\n000 0.240000')

    async def test_timeout_is_not_reported_as_no_matching_results(self):
        with patch('core.web_search.provider.os.getenv', return_value='test-key'), \
             patch('core.web_search.provider.post_search', new_callable=AsyncMock,
                   side_effect=httpx.ConnectTimeout('test')) as request:
            evidence = await search_evidence({}, '查询示例', 'request')
        request.assert_awaited_once()
        self.assertEqual('failed', evidence['searchStatus'])
        self.assertEqual('connect_timeout', evidence['failureKind'])
        self.assertIn('超时', evidence['warning'])
        self.assertEqual([], evidence['items'])


if __name__ == '__main__':
    unittest.main()
