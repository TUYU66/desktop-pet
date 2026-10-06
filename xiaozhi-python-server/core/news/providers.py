"""HTTP adapters only: no routing, dialogue state or LLM calls."""
import asyncio
import copy
import json
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import httpx
from config.logger import setup_logging
from .catalog import CHANNEL_MAP

logger = setup_logging()
TAG = __name__
_CACHE = OrderedDict()

def _text(value, limit=300):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', '', str(value or ''))).strip()[:limit]


def _url(value):
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlparse(value)
        return value[:2000] if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username else ''
    except ValueError:
        return ''


def _date(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000 if value > 100000000000 else value, timezone.utc)
        if not isinstance(value, str) or not value.strip():
            return None
        if re.fullmatch(r'\d{8}T\d{6}Z', value):
            return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # No guessing the timezone for upstream strings without an offset.
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


async def _json(client, url, params, cache_seconds):
    key = (url, json.dumps(params, sort_keys=True))
    cached = _CACHE.get(key)
    now = time.monotonic()
    if cached and cached[0] > now:
        _CACHE.move_to_end(key)
        return copy.deepcopy(cached[1])
    # Passing even params={} replaces the query embedded in the URL in HTTPX.
    # Preserve configured query parameters and explicitly merge new ones.
    request_url = httpx.URL(url)
    if params:
        request_url = request_url.copy_merge_params(params)
    response = await client.get(request_url)
    if response.status_code == 403:
        # Inspect the running server's environment, not the developer shell's.
        # Presence alone does not prove use: NO_PROXY may bypass the proxy.
        proxy_env = [name for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                                      'http_proxy', 'https_proxy', 'all_proxy')
                     if os.getenv(name)]
        logger.bind(tag=TAG).warning(
            '热点接口拒绝访问: host={}, status=403, content_type={}, server={}, '
            'cf_mitigated={}, proxy_env_present={}, no_proxy_present={}',
            response.url.host,
            _text(response.headers.get('content-type'), 100),
            _text(response.headers.get('server'), 80),
            _text(response.headers.get('cf-mitigated'), 40),
            proxy_env, bool(os.getenv('NO_PROXY') or os.getenv('no_proxy')))
    response.raise_for_status()
    if len(response.content) > 2 * 1024 * 1024:
        raise ValueError('news_response_too_large')
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError('news_response_invalid')
    # Error documents are never stored as successful news responses.
    if data.get('status') in ('error', 'failed') or data.get('error'):
        raise ValueError('news_provider_error')
    if cache_seconds:
        _CACHE[key] = (now + cache_seconds, copy.deepcopy(data))
        _CACHE.move_to_end(key)
        while len(_CACHE) > 64:
            _CACHE.popitem(last=False)
    return data


async def _hot(client, config, names, hours, count, ttl):
    if not names:
        raise ValueError('没有配置可用的热点来源')
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    async def fetch(name):
        url = config.get('url') or 'https://newsnow.busiyi.world/api/s?id='
        # Preserve the existing deployment's configurable NewsNow URL.
        if url.endswith('id='):
            data = await _json(client, url + CHANNEL_MAP[name], {}, ttl)
        else:
            data = await _json(client, url, {'id': CHANNEL_MAP[name]}, ttl)
        if not isinstance(data.get('items'), list):
            raise ValueError('hot_list_missing')
        updated = _date(data.get('updatedTime'))
        if updated and (now - updated > timedelta(hours=min(hours, 24)) or updated > now + timedelta(minutes=10)):
            raise ValueError('hot_list_stale')
        items = []
        for rank, row in enumerate(data['items'][:40], 1):
            if not isinstance(row, dict):
                continue
            title, url = _text(row.get('title')), _url(row.get('url'))
            published = _date(row.get('pubDate'))
            if not title or not url or published and not since <= published <= now + timedelta(minutes=10):
                continue
            items.append({'title': title, 'source': name, 'url': url,
                          'sourceRank': rank,
                          'publishedAt': published.isoformat() if published else None,
                          'listUpdatedAt': updated.isoformat() if updated else None})
        return items

    results = await asyncio.gather(*(fetch(name) for name in names), return_exceptions=True)
    warnings = []
    for name, result in zip(names, results):
        if not isinstance(result, Exception):
            continue
        # Do not log raw response bodies or URLs, which may contain credentials.
        if isinstance(result, httpx.HTTPStatusError):
            reason = 'HTTP ' + str(result.response.status_code)
        elif isinstance(result, json.JSONDecodeError):
            reason = '返回内容不是有效 JSON'
        elif isinstance(result, (httpx.TimeoutException, asyncio.TimeoutError)):
            reason = '请求超时'
        elif isinstance(result, ValueError):
            reason = {
                'news_response_too_large': '响应内容过大',
                'news_response_invalid': '响应结构异常',
                'news_provider_error': '上游服务返回错误',
                'hot_list_missing': '响应缺少榜单 items',
                'hot_list_stale': '榜单更新时间过期或异常',
            }.get(str(result), '数据解析失败')
        else:
            reason = '请求或解析异常'
        logger.bind(tag=TAG).warning('热点来源失败: source={}, type={}, reason={}',
                                     name, type(result).__name__, reason)
        warnings.append(name + ' 暂不可用（' + reason + '）')
    lists = [result for result in results if isinstance(result, list)]
    if not lists:
        raise ValueError('；'.join(warnings) or '新闻来源暂时无法访问，请稍后重试')
    # Round-robin prevents the first source from occupying the whole bulletin.
    merged, titles, urls = [], set(), set()
    for index in range(max((len(items) for items in lists), default=0)):
        for items in lists:
            if index >= len(items):
                continue
            item = items[index]
            title_key = re.sub(r'\W+', '', item['title']).casefold()
            if title_key in titles or item['url'] in urls:
                continue
            titles.add(title_key); urls.add(item['url']); merged.append(item)
            if len(merged) >= count:
                return merged, warnings
    return merged, warnings


