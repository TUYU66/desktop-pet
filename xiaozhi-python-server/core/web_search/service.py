"""One explicitly authorized Web Search per real user turn."""
import copy
import json
import re
import time
import uuid
from datetime import datetime
from config.logger import setup_logging
from .provider import search_evidence

logger = setup_logging()
TAG = __name__
AFFIRM = re.compile(r'^(?:好|好的|好啊|好呀|行|行啊|可以|可以的|可以啊|要|要的|要啊|要呀|是的|是|需要|需要的|同意|嗯|嗯嗯|查吧|搜吧|查一下吧|继续查|yes|ok|sure)[。！!，,？?\s]*$', re.I)
ASK = re.compile(r'(?:要不要|是否|需要|要我|可以).{0,35}(?:联网|上网|查询|搜索|查一下|搜一下|核实)|(?:联网|上网|查询|搜索|查一下|搜一下|核实).{0,25}(?:吗|么|？|\?)')


def _explicit(text):
    # Conservative gate: uncertainty asks permission rather than inferring it.
    text = re.sub(r'[“「『"].*?[”」』"]', '', text)
    if re.search(r'别(?:再)?(?:查|搜)|不(?:要|用|必|需要).{0,8}(?:查|搜|联网|上网|核实)|(?:do not|don.t)\s+(?:search|look up)', text, re.I):
        return False
    return bool(re.search(r'(?:帮我|给我|替我|请|麻烦).{0,12}(?:搜|查|核实)|^(?:上网|联网).{0,8}(?:搜|查|核实)|^(?:搜索|查询|核实|搜一下|查一下|查查|搜搜)|(?:search (?:for|the web)|look up|fact.check)', text, re.I))


def _context(conn):
    dialogue = getattr(conn, 'dialogue', None)
    if dialogue is None:
        return None, ''
    with dialogue._lock:
        messages = list(dialogue.dialogue)
    users = [i for i, m in enumerate(messages) if m.role == 'user' and m.is_user_input and not m.is_temporary]
    if not users:
        return None, ''
    index = users[-1]
    current = messages[index]
    text = current.content or ''
    if text.startswith('{'):
        try:
            wrapped = json.loads(text)
            text = wrapped.get('content', '') if isinstance(wrapped, dict) else ''
        except (ValueError, TypeError):
            pass
    # Only a preceding answer from the immediately previous turn can ask consent.
    previous_start = users[-2] + 1 if len(users) > 1 else 0
    previous = ''.join(m.content or '' for m in messages[previous_start:index]
                       if m.role == 'assistant' and not m.is_temporary and not m.tool_calls
                       and 0 <= (datetime.now() - m.created_at).total_seconds() < 300)
    fresh = 0 <= (datetime.now() - current.created_at).total_seconds() < 300
    previous_user_id = messages[users[-2]].uniq_id if len(users) > 1 else None
    return (current.uniq_id, str(text), fresh, previous_user_id), previous


def _config(conn):
    result = {}
    # Read existing credentials for migration, without changing or exposing them.
    for plugin in ('get_news_from_newsnow', 'web_search'):
        for cfg in (getattr(conn, 'common_config', {}) or {}, conn.config):
            source = cfg.get('plugins', {}).get(plugin, {})
            result.update({key: source[key] for key in ('search_api_key', 'search_transport','search_timeout','search_connect_timeout') if key in source})
    return result


async def search(conn, query):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
        return {'status': 'failed', 'items': [], 'message': '请提供不超过500字的完整查证问题'}
    query = query.strip()
    current, previous = _context(conn)
    session = getattr(conn, 'session_id', None)
    # Reuse an already authorized request before re-evaluating consent. A tool
    # retry in the same turn must not prompt again after pending was consumed.
    turn_key = (session, current[0]) if current else None
    last = getattr(conn, 'web_search_last', None)
    if current and last and last['turn'] == turn_key:
        return copy.deepcopy(last['result'])
    pending = getattr(conn, 'web_search_pending', None)
    valid_pending = (pending and pending['session'] == session
                     and time.monotonic() - pending['savedAt'] < 300
                     and current and pending['turnId'] in (current[0], current[3]))
    if not valid_pending:
        pending = None
    authorized = current and current[2] and _explicit(current[1])
    confirmed = current and current[2] and AFFIRM.fullmatch(current[1].strip()) and ASK.search(previous)
    if confirmed and valid_pending and current[0] != pending['turnId']:
        query = pending['query']
        authorized = True
    elif confirmed and not pending:
        # A normal assistant reply may ask consent without invoking any tool.
        authorized = True
    if not authorized:
        conn.web_search_pending = {'session': session, 'savedAt': time.monotonic(), 'query': query,
                                   'turnId': current[0] if current else None}
        logger.bind(tag=TAG).info('Web Search 等待用户确认：未发送搜索请求')
        return {'status': 'confirmation_required', 'items': [],
                'message': '要我帮你联网查询一下吗？'}
    conn.web_search_pending = None
    # Mark before await so repeated tool calls cannot duplicate a paid request.
    state = {'turn': turn_key, 'result': {'status': 'pending', 'items': [], 'message': '本轮查询正在进行，请勿重复请求'}}
    conn.web_search_last = state
    request_id = uuid.uuid4().hex[:12]
    try:
        logger.bind(tag=TAG).info('Web Search 已授权: request_id={}, authorization={}', request_id,
                                 'confirmation' if confirmed else 'explicit_request')
        data = await search_evidence(_config(conn), query, request_id)
        latest, _ = _context(conn)
        if getattr(conn, 'session_id', None) != session or not latest or latest[0] != current[0]:
            result = {'status': 'superseded', 'items': [], 'message': '用户问题已切换，本次搜索结果不再使用'}
        else:
            result = {'status': data['searchStatus'], 'query': query, 'items': data['items'],
                      'message': data.get('warning'), 'requestId': request_id}
        state['result'] = copy.deepcopy(result)
        return result
    except Exception as exc:
        logger.bind(tag=TAG).warning('Web Search 未完成: request_id={}, type={}', request_id, type(exc).__name__)
        state['result'] = {'status': 'failed', 'items': [], 'message': '联网查询未能完成，不能据此判断有无相关资料'}
        return copy.deepcopy(state['result'])
