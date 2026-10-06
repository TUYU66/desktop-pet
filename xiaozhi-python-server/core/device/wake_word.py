"""Single-device wake phrase delivery. Only a matching device ACK means applied."""
import asyncio
import json
import logging
import re
import uuid


def prepare_word(word, pronunciation=''):
    if not isinstance(word, str) or (word and not re.fullmatch(r'[\u4e00-\u9fff]{3,8}', word)):
        raise ValueError('唤醒词请填写 3～8 个汉字，或留空关闭')
    if not word:
        return ''
    if pronunciation:
        if (len(pronunciation) > 63 or not re.fullmatch(r'[a-z]+(?: [a-z]+)*', pronunciation)
                or len(pronunciation.split()) != len(word)):
            raise ValueError('读音请用空格分隔每个字的无声调拼音')
        return pronunciation
    from pypinyin import lazy_pinyin
    syllables = lazy_pinyin(word, errors=lambda text: list(text))
    pinyin = ' '.join(syllables)
    if len(syllables) != len(word) or not re.fullmatch(r'[a-z]+(?: [a-z]+)*', pinyin) or len(pinyin) > 63:
        raise ValueError('这个词暂时无法转换为设备支持的读音，请换一个词')
    return pinyin


def accept_ack(conn, message):
    pending = getattr(conn, '_wake_pending', None)
    if not pending:
        return
    request_id, word, future = pending
    if message.get('requestId') == request_id and message.get('word') == word and not future.done():
        future.set_result(message.get('status'))


async def synchronize(conn, word, pronunciation=''):
    if not (getattr(conn, 'features', None) or {}).get('dynamic_wake_word'):
        conn.wake_word_status = {'state': 'unsupported', 'word': word}
        return
    if not hasattr(conn, '_wake_lock'):
        conn._wake_lock = asyncio.Lock()
    async with conn._wake_lock:
        current = getattr(conn, 'wake_word_status', {})
        if current.get('state') == 'applied' and current.get('word') == word and current.get('pronunciation', '') == pronunciation:
            return
        conn.wake_word_status = {'state': 'pending', 'word': word}
        try:
            pinyin = prepare_word(word, pronunciation)
            request_id = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            conn._wake_pending = (request_id, word, future)
            await conn.websocket.send(json.dumps({'type': 'wake_word_config', 'requestId': request_id,
                                                 'word': word, 'pinyin': pinyin}, ensure_ascii=False))
            result = await asyncio.wait_for(future, 5)
            if result == 'applied':
                conn.wake_word_status = {'state': 'applied', 'word': word, 'pinyin': pinyin, 'pronunciation': pronunciation}
                # Do not classify the new wake phrase as a normal user question.
                conn.config['customWakeWord'] = word
            else:
                conn.wake_word_status = {'state': 'failed', 'word': word, 'reason': str(result)}
        except asyncio.TimeoutError:
            conn.wake_word_status = {'state': 'pending', 'word': word}
        except Exception as error:
            conn.wake_word_status = {'state': 'failed', 'word': word,
                                    'reason': 'dependency_missing' if isinstance(error, ImportError) else 'delivery_failed'}
        finally:
            conn._wake_pending = None


class WakeWordSync:
    def __init__(self, config, server):
        self.config, self.server = config, server

    async def run(self):
        import os
        import httpx
        java_url = os.environ.get("JAVA_SERVER_URL", "http://localhost:8000")
        refresh_failed = False
        while True:
            try:
                # Read the persisted setting each cycle: recover missed reload
                # notifications and Python restarts, including clearing the word.
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(f"{java_url}/xiaozhi/api/user/config",
                                                headers={"Service-Key": "xiaozhi-python"})
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get('code') != 0 or not isinstance(payload.get('data'), dict):
                        raise ValueError('Configuration unavailable')
                    self.config['customWakeWord'] = payload['data'].get('customWakeWord', '')
                    self.config['customWakePinyin'] = payload['data'].get('customWakePinyin', '')
                refresh_failed = False
                devices = self.server.device_handlers if self.server else {}
                if len(devices) == 1 and 'customWakeWord' in self.config:
                    conn = next(iter(devices.values()))
                    if getattr(conn, 'features', None) is not None:
                        await synchronize(conn, self.config['customWakeWord'], self.config.get('customWakePinyin', ''))
            except Exception as error:
                if not refresh_failed:
                    logging.getLogger(__name__).warning('Wake word sync will retry: %s', type(error).__name__)
                refresh_failed = True
            await asyncio.sleep(5)

    async def status(self, request):
        from aiohttp import web
        expected = request.query.get('word', '')
        devices = self.server.device_handlers if self.server else {}
        if not devices:
            data = {'state': 'offline', 'word': expected}
        elif len(devices) != 1:
            data = {'state': 'multiple_devices', 'word': expected}
        else:
            conn = next(iter(devices.values()))
            data = getattr(conn, 'wake_word_status', {'state': 'pending', 'word': expected})
            if data.get('word') != expected:
                data = {'state': 'pending', 'word': expected}
        return web.json_response({'code': 0, 'data': data})
