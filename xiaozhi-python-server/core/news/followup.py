"""Resolve ordinal detail questions against the actual, recent platform bulletin."""
import re
import time
from core.news.catalog import CHANNEL_MAP, SOURCE_ALIASES


def user_turn_ids(conn):
    dialogue = getattr(conn, 'dialogue', None)
    if dialogue is None:
        return []
    with dialogue._lock:
        return [m.uniq_id for m in dialogue.dialogue
                if m.role == 'user' and m.is_user_input and not m.is_temporary]


def detail_search_query(conn, text):
    if not isinstance(text, str):
        return None
    if re.search(r'不要|不用|别查|别搜|不必|不需要|提醒|记忆|歌单|歌曲', text):
        return None
    match = re.search(r'第\s*([一二两三四五12345])\s*(?:条|个|项)', text)
    if not match or len(re.findall(r'第\s*[一二两三四五12345]\s*(?:条|个|项)', text)) != 1:
        return None
    # A detail question or a bare choice, not arbitrary mention of a number.
    choice = re.fullmatch(r'(?:就|那就|看|看看|讲|讲讲|说说|说一说|说一下|选)?\s*第\s*[一二两三四五12345]\s*(?:条|个|项)[吧呢。？！!?\s]*', text)
    if not choice and not re.search(r'讲|说说|说一说|说一下|展开|具体|详细|详情|内容|怎么回事|发生|介绍|解释|什么|啥|查|搜|了解', text):
        return None
    # A number inside a new subject (e.g. Python's second argument) is not
    # an elliptical news selection, even immediately after a bulletin.
    explicit_news = bool(re.search(r'新闻|热榜|榜单|热点', text))
    if not explicit_news and not re.fullmatch(
            r'(?:请|帮我)?(?:就|那就|看|看看|讲|讲讲|说说|说一说|说一下|选|查|查查|查一下|搜一下|介绍一下|解释一下)?'
            r'\s*第\s*[一二两三四五12345]\s*(?:条|个|项)'
            r'(?:详细讲讲|详细说说|详细说一下|展开说|具体一点|是什么内容|是什么|什么内容|怎么回事|的详情|详情|的内容|内容|讲讲|说说)?'
            r'[吧呢。？！!?\s]*', text):
        return None
    bulletin = getattr(conn, 'news_bulletin', None)
    if (not bulletin or bulletin.get('session') != getattr(conn, 'session_id', None)
            or not 0 <= time.monotonic() - bulletin.get('savedAt', -10000) < 1800):
        return None
    turns = user_turn_ids(conn)
    anchor = bulletin.get('topicTurnId')
    if not explicit_news and (not turns or not anchor or anchor not in turns[-2:]):
        return None
    index = int(match[1]) if match[1].isdigit() else {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5}[match[1]]
    item = next((row for row in bulletin.get('items', []) if row.get('itemIndex') == index), None)
    if not item:
        return None
    source = item.get('source')
    source = SOURCE_ALIASES.get(source, source)
    source = next((name for name, code in CHANNEL_MAP.items() if source == code), source)
    if source not in CHANNEL_MAP:
        return None
    title = item.get('title')
    if not isinstance(title, str) or not title.strip():
        return None
    if turns:
        bulletin['topicTurnId'] = turns[-1]
    return f'{source}热榜话题：{title.strip()[:300]}。查询事件详情、最新进展和可靠来源。用户追问：{text[:100]}'
