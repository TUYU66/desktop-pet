import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.device.wake_word import prepare_word, synchronize, accept_ack, WakeWordSync


class WakeWordTests(unittest.IsolatedAsyncioTestCase):
    def conn(self):
        return SimpleNamespace(features={'dynamic_wake_word': True}, config={},
                               websocket=SimpleNamespace(send=AsyncMock()))

    def test_chinese_conversion_and_clear(self):
        self.assertEqual(prepare_word('你好朋友'), 'ni hao peng you')
        self.assertEqual(prepare_word('小鹿小鹿'), 'xiao lu xiao lu')
        self.assertEqual(prepare_word(''), '')
        for word in ('小鹿', 'hello', '小智123', '你好 小智', '一二三四五六七八九', None):
            with self.assertRaises(ValueError):
                prepare_word(word)

    async def test_ack_required_and_old_ack_ignored(self):
        conn = self.conn()
        task = asyncio.create_task(synchronize(conn, '你好朋友'))
        await asyncio.sleep(0)
        self.assertEqual(conn.wake_word_status['state'], 'pending')
        request = json.loads(conn.websocket.send.call_args.args[0])
        accept_ack(conn, {**request, 'requestId': 'stale', 'status': 'applied'})
        self.assertFalse(conn._wake_pending[2].done())
        accept_ack(conn, {**request, 'word': '小鹿小鹿', 'status': 'applied'})
        self.assertFalse(conn._wake_pending[2].done())
        accept_ack(conn, {**request, 'status': 'applied'})
        await task
        self.assertEqual(conn.wake_word_status, {'state': 'applied', 'word': '你好朋友', 'pinyin': 'ni hao peng you', 'pronunciation': ''})
        self.assertEqual(conn.config['customWakeWord'], '你好朋友')
        await synchronize(conn, '你好朋友')
        self.assertEqual(conn.websocket.send.call_count, 1)

    async def test_clear_is_delivered_and_confirmed(self):
        conn = self.conn()
        conn.config['customWakeWord'] = '你好朋友'
        async def ack(payload):
            request = json.loads(payload)
            self.assertEqual(request['pinyin'], '')
            accept_ack(conn, {**request, 'status': 'applied'})
        conn.websocket.send.side_effect = ack
        await synchronize(conn, '')
        self.assertEqual(conn.config['customWakeWord'], '')

    async def test_storage_failure_not_applied(self):
        conn = self.conn()
        conn.config['customWakeWord'] = '你好朋友'
        async def ack(payload):
            accept_ack(conn, {**json.loads(payload), 'status': 'storage_error'})
        conn.websocket.send.side_effect = ack
        await synchronize(conn, '小鹿小鹿')
        self.assertEqual(conn.wake_word_status['state'], 'failed')
        self.assertEqual(conn.config['customWakeWord'], '你好朋友')

    async def test_timeout_remains_pending(self):
        conn = self.conn()
        with patch('core.device.wake_word.asyncio.wait_for', AsyncMock(side_effect=asyncio.TimeoutError)):
            await synchronize(conn, '你好朋友')
        self.assertEqual(conn.wake_word_status['state'], 'pending')
        self.assertNotIn('customWakeWord', conn.config)

    async def test_reconnect_requires_new_ack(self):
        old = self.conn()
        old.wake_word_status = {'state': 'applied', 'word': '你好朋友'}
        fresh = self.conn()
        async def ack(payload):
            accept_ack(fresh, {**json.loads(payload), 'status': 'applied'})
        fresh.websocket.send.side_effect = ack
        await synchronize(fresh, '你好朋友')
        fresh.websocket.send.assert_awaited_once()

    async def test_old_firmware_does_not_receive_config(self):
        conn = self.conn()
        conn.features = {}
        await synchronize(conn, '你好朋友')
        self.assertEqual(conn.wake_word_status['state'], 'unsupported')
        conn.websocket.send.assert_not_awaited()

    async def test_status_offline_multiple_and_stale(self):
        server = SimpleNamespace(device_handlers={})
        service = WakeWordSync({}, server)
        request = SimpleNamespace(query={'word': '小鹿小鹿'})
        async def state():
            return json.loads((await service.status(request)).text)['data']['state']
        self.assertEqual(await state(), 'offline')
        conn = self.conn()
        conn.wake_word_status = {'state': 'applied', 'word': '你好朋友'}
        server.device_handlers = {'one': conn}
        self.assertEqual(await state(), 'pending')
        server.device_handlers['two'] = self.conn()
        self.assertEqual(await state(), 'multiple_devices')

    async def test_offline_save_reconciles_on_connect_without_browser(self):
        server = SimpleNamespace(device_handlers={})
        service = WakeWordSync({}, server)
        conn = self.conn()
        async def ack(payload):
            accept_ack(conn, {**json.loads(payload), 'status': 'applied'})
        conn.websocket.send.side_effect = ack
        response = Mock()
        response.json.return_value = {'code': 0, 'data': {'customWakeWord': '小鹿小鹿'}}
        client = AsyncMock()
        client.get.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        cycles = 0
        async def next_cycle(_):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                server.device_handlers['one'] = conn
            else:
                raise asyncio.CancelledError
        with patch('httpx.AsyncClient', return_value=context), patch('core.device.wake_word.asyncio.sleep', next_cycle):
            with self.assertRaises(asyncio.CancelledError):
                await service.run()
        self.assertEqual(conn.wake_word_status, {'state': 'applied', 'word': '小鹿小鹿', 'pinyin': 'xiao lu xiao lu', 'pronunciation': ''})

    async def test_same_word_new_pronunciation_requires_new_device_ack(self):
        conn = self.conn()
        async def ack(payload):
            accept_ack(conn, {**json.loads(payload), 'status': 'applied'})
        conn.websocket.send.side_effect = ack
        await synchronize(conn, '快乐小乐', 'kuai le xiao le')
        await synchronize(conn, '快乐小乐', 'kuai le xiao yue')
        self.assertEqual(conn.websocket.send.await_count, 2)
        self.assertEqual(conn.wake_word_status['pinyin'], 'kuai le xiao yue')
        for pronunciation in ('kuai le', 'kuai le xiao yue4', 'kuai le xiao  yue'):
            with self.assertRaises(ValueError):
                prepare_word('快乐小乐', pronunciation)

    async def test_failed_config_fetch_never_clears_device(self):
        conn = self.conn()
        server = SimpleNamespace(device_handlers={'one': conn})
        service = WakeWordSync({'customWakeWord': '你好朋友'}, server)
        context = AsyncMock()
        context.__aenter__.side_effect = RuntimeError('offline')
        with patch('httpx.AsyncClient', return_value=context), patch('core.device.wake_word.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await service.run()
        conn.websocket.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
