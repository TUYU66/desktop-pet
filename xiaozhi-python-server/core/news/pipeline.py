"""NewsNow lists only: no search, semantic selection or detail routing."""
import asyncio
import copy
import json
import time
import uuid
from datetime import datetime, timezone
import httpx
from config.logger import setup_logging
from .catalog import _sources
from .providers import _hot
logger = setup_logging()
TAG = __name__


def config_for(conn):
    shared = getattr(conn, 'common_config', {}) or {}
    config = dict(shared.get('plugins', {}).get('get_news_from_newsnow', {}))
    config.update(conn.config.get('plugins', {}).get('get_news_from_newsnow', {}))
    return config


async def run(conn, arguments):
    request_id = uuid.uuid4().hex[:12]
    session = getattr(conn, 'session_id', None)
    conn.news_request_id = request_id
    result = {'requestId': request_id, 'provider': 'newsnow', 'items': [],
              'status': 'failed', 'fetchedAt': datetime.now(timezone.utc).isoformat()}
    try:
        if set(arguments) - {'category', 'sources', 'count'}:
            raise ValueError('热点工具只支持category/sources/count。知识解释直接结合上下文回答；明确请求联网查证才使用web_search。')
        count = arguments.get('count', 3)
        if type(count) is not int or not 1 <= count <= 5:
            raise ValueError('榜单条数应为1～5')
        config = config_for(conn)
        names = _sources(config, arguments.get('category', 'general'), None, arguments.get('sources'))
        ttl = max(0, min(int(config.get('cache_seconds', 300)), 600))
        logger.bind(tag=TAG).info('NewsNow 看榜开始: request_id={}, sources={}', request_id, names)
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=3), follow_redirects=True) as client:
            items, warnings = await asyncio.wait_for(_hot(client, config, names, 24, count, ttl), timeout=16)
        if getattr(conn, 'session_id', None) != session or conn.news_request_id != request_id:
            result.update(status='superseded', message='会话或查询已切换')
            return result
        for index, item in enumerate(items, 1):
            item['itemIndex'] = index
        conn.news_bulletin = {'session': session, 'savedAt': time.monotonic(), 'items': copy.deepcopy(items)}
        result.update(items=items, warnings=warnings, requestedSources=names,
                      status=('partial' if warnings else 'ok') if items else 'empty',
                      message=None if items else '当前榜单没有可用条目，不代表相关圈子没有讨论')
        logger.bind(tag=TAG).info('NewsNow 看榜完成: request_id={}, status={}, items={}', request_id, result['status'], len(items))
    except (httpx.HTTPError, asyncio.TimeoutError, TimeoutError, ValueError, TypeError) as exc:
        reason = str(exc)[:200] if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else '榜单请求未完成'
        result.update(message=reason)
        logger.bind(tag=TAG).warning('NewsNow 看榜失败: request_id={}, type={}', request_id, type(exc).__name__)
    return result
