"""Tavily adapter; called only after the separate tool checks authorization."""
import asyncio
import json
import os
import re
import time
from urllib.parse import urlparse
import httpx
from config.logger import setup_logging
from .transport import post_search, transport_name
logger = setup_logging()
TAG = __name__

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


async def search_evidence(config, query, request_id):
    mode, stage = 'web_search', 'explicit'
    payload = {'provider': 'tavily', 'query': query, 'items': [],
               'requestId': request_id}
    search_params = {'query': query, 'topic': 'general', 'search_depth': 'basic',
                     'max_results': 5, 'include_answer': False, 'include_raw_content': False}
    payload.update(searchScope='web', searchStage=stage)
    key = os.getenv('TAVILY_API_KEY') or config.get('search_api_key')
    search_id = request_id
    if not key:
        logger.bind(tag=TAG).warning('Tavily 搜索跳过: search_id={}, status=not_configured（未配置 API Key）', search_id)
        payload.update(searchStatus='not_configured', warning='尚未配置Web Search的API Key，本次没有发出联网查询。')
        return payload
    started = time.monotonic()
    http_status = None
    logger.bind(tag=TAG).info(
        'Tavily 搜索开始: search_id={}, mode={}, stage={}, scope=web',
        search_id, mode, stage)
    try:
        transport = transport_name(config)
        logger.bind(tag=TAG).info('Tavily 请求通道: search_id={}, transport={}', search_id, transport)
        timeout=config.get('search_timeout',24)
        connect_timeout=config.get('search_connect_timeout',6)
        logger.bind(tag=TAG).info('Tavily 超时预算: search_id={}, total_seconds={}, connect_seconds={}, proxy_env_present={}',
            search_id,timeout,connect_timeout,
            any(os.getenv(name) for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')))
        response = await post_search(transport, key, search_params,timeout,connect_timeout)
        http_status = response.status_code
        response.raise_for_status()
        if len(response.content) > 2 * 1024 * 1024:
            raise ValueError('search_response_too_large')
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('results'), list):
            raise ValueError('search_response_invalid')
        seen = set()
        for row in data['results'][:5]:
            if not isinstance(row, dict):
                continue
            url, title, excerpt = _url(row.get('url')), _text(row.get('title')), _text(row.get('content'), 1800)
            if not url or not title or not excerpt or url in seen:
                continue
            seen.add(url)
            payload['items'].append({'title': title, 'url': url, 'source': urlparse(url).hostname,
                                     'excerpt': excerpt, 'evidenceType': 'search_excerpt',
                                     'publishedDate': _text(row.get('published_date'), 100) or None,
                                     'searchStage': stage})
        payload['searchStatus'] = 'ok' if payload['items'] else 'empty'
        logger.bind(tag=TAG).info(
            'Tavily 搜索完成: search_id={}, stage={}, status={}, http_status={}, returned_results={}, '
            'valid_results={}, source_domains={}, elapsed_ms={}',
            search_id, stage, payload['searchStatus'], http_status, len(data['results']),
            len(payload['items']), sorted({item['source'] for item in payload['items']}),
            round((time.monotonic() - started) * 1000))
    except asyncio.CancelledError:
        logger.bind(tag=TAG).info('Tavily 搜索取消: search_id={}, elapsed_ms={}',
                                search_id, round((time.monotonic() - started) * 1000))
        raise
    except (httpx.HTTPError, asyncio.TimeoutError, TimeoutError, ValueError) as exc:
        # Only log controlled reasons, never credentials, response bodies or URLs.
        if isinstance(exc, httpx.RemoteProtocolError):
            reason = 'remote_protocol_error'
        elif isinstance(exc, httpx.HTTPStatusError):
            reason = 'http_error'
        elif isinstance(exc,httpx.ConnectTimeout):
            reason='connect_timeout'
        elif isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
            reason = 'timeout'
        elif isinstance(exc, json.JSONDecodeError):
            reason = 'invalid_json'
        elif isinstance(exc, ValueError):
            code = str(exc)
            safe_codes = ('search_response_too_large', 'search_response_invalid', 'curl_not_available',
                          'curl_start_failed', 'curl_response_invalid', 'search_transport_invalid', 'search_key_invalid')
            reason = code if code in safe_codes or re.fullmatch(r'curl_exit_-?[0-9]+', code) else 'invalid_data'
        else:
            reason = 'network_error'
        logger.bind(tag=TAG).warning(
            'Tavily 搜索失败: search_id={}, stage={}, type={}, reason={}, http_status={}, elapsed_ms={}',
            search_id, stage, type(exc).__name__, reason, http_status,
            round((time.monotonic() - started) * 1000))
        if isinstance(exc, httpx.TransportError):
            proxy_env = [name for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                                          'http_proxy', 'https_proxy', 'all_proxy') if os.getenv(name)]
            # Record only known protocol markers, never raw exceptions or proxy URLs.
            disconnected = 'disconnected without sending a response' in str(exc).lower()
            logger.bind(tag=TAG).warning(
                'Tavily 连接诊断: search_id={}, disconnected_without_response={}, '
                'cause_type={}, proxy_env_present={}, no_proxy_present={}',
                search_id, disconnected, type(exc.__cause__).__name__ if exc.__cause__ else None,
                proxy_env, bool(os.getenv('NO_PROXY') or os.getenv('no_proxy')))
        warning=('这次联网搜索超时了，还没查到结果，过会儿再试吧。' if reason in ('timeout','connect_timeout')
                 else '搜索服务这次没接受请求，你看一下密钥或额度是否正常。' if http_status in (401,403,429)
                 else '这次联网搜索没完成，还没查到结果，过会儿再试吧。')
        payload.update(searchStatus='failed', failureKind=reason,warning=warning)
    return payload
