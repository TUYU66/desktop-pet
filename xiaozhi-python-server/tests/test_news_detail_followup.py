"""Recent Douyin ordinal follow-ups authorize one title-based Tavily lookup."""
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from core.news.followup import detail_search_query
from core.utils.dialogue import Dialogue, Message
from core.web_search.service import search


def connection(text='第二条详细讲讲'):
    dialogue = Dialogue()
    origin = Message(role='user', content='看看新闻热榜')
    dialogue.put(origin)
    dialogue.put(Message(role='user', content=text))
    return NS(session_id='s', config={}, common_config={}, dialogue=dialogue,
              news_bulletin={'session': 's', 'savedAt': time.monotonic(), 'topicTurnId': origin.uniq_id, 'items': [
                  {'itemIndex': 1, 'source': '抖音', 'title': '第一条真实话题'},
                  {'itemIndex': 2, 'source': '抖音', 'title': '第二条真实话题'},
              ]})


class DetailFollowupTests(unittest.TestCase):
    def test_new_subject_does_not_reuse_news(self):
        for text in ('你刚才给的第二个学习方法详细讲讲', 'Python第二个参数是什么意思'):
            self.assertIsNone(detail_search_query(connection(text), text))

    def test_topic_switch_breaks_implicit_followup_but_explicit_return_works(self):
        conn = connection('推荐两个学习方法')
        conn.dialogue.put(Message(role='user', content='第二条详细讲讲'))
        self.assertIsNone(detail_search_query(conn, '第二条详细讲讲'))
        conn.dialogue.put(Message(role='user', content='刚才新闻第二条详细讲讲'))
        self.assertIn('第二条真实话题', detail_search_query(conn, '刚才新闻第二条详细讲讲'))

    def test_consecutive_news_followups_keep_the_topic(self):
        conn = connection()
        self.assertIsNotNone(detail_search_query(conn, '第二条详细讲讲'))
        conn.dialogue.put(Message(role='user', content='讲讲第一条'))
        self.assertIn('第一条真实话题', detail_search_query(conn, '讲讲第一条'))

    def test_ordinal_uses_original_title_without_refetching_rankings(self):
        for text in ('第二条', '讲讲第二条', '第二条是什么内容', '第2个详细说说',
                     '说一说第二条', '说一下第二条', '第二条展开说', '第二条具体一点'):
            with self.subTest(text=text):
                query = detail_search_query(connection(text), text)
                self.assertIn('第二条真实话题', query)
                self.assertNotIn('第一条真实话题', query)

    def test_unrelated_negated_ambiguous_and_unknown_choices_do_not_search(self):
        for text in ('第二条提醒取消', '不要搜索第二条', '第一条和第二条都讲讲',
                     '第五条详细讲讲', '今天好累', '我的第二条记忆是什么'):
            with self.subTest(text=text):
                self.assertIsNone(detail_search_query(connection(text), text))

    def test_expired_other_session_or_unknown_platform_is_not_used(self):
        for change in ('expired', 'session', 'platform'):
            conn = connection()
            if change == 'expired':
                conn.news_bulletin['savedAt'] -= 1801
            elif change == 'session':
                conn.news_bulletin['session'] = 'old'
            else:
                conn.news_bulletin['items'][1]['source'] = '未知来源'
            self.assertIsNone(detail_search_query(conn, '第二条详细讲讲'))

    def test_supported_platforms_and_aliases_use_their_actual_source(self):
        for source in ('哔哩哔哩', 'B站', '微博', '知乎', 'bilibili-hot-search', 'douyin'):
            conn = connection('说一说第二条')
            conn.news_bulletin['items'][1]['source'] = source
            query = detail_search_query(conn, '说一说第二条')
            self.assertIn('第二条真实话题', query)
            self.assertIn('哔哩哔哩' if source in ('哔哩哔哩', 'B站', 'bilibili-hot-search')
                          else '抖音' if source == 'douyin' else source, query)


class SearchAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulletin_is_bound_to_fetching_user_turn(self):
        from core.news.pipeline import run
        conn = connection('看看新闻')
        origin = conn.dialogue.dialogue[-1].uniq_id
        with patch('core.news.pipeline._hot', new_callable=AsyncMock,
                   return_value=([{'source': '抖音', 'title': '新话题'}], [])):
            result = await run(conn, {'sources': ['抖音']})
        self.assertEqual('ok', result['status'])
        self.assertEqual(origin, conn.news_bulletin['topicTurnId'])
        conn.dialogue.put(Message(role='user', content='讲讲第一条'))
        self.assertIn('新话题', detail_search_query(conn, '讲讲第一条'))

    async def test_late_bulletin_cannot_replace_topic_after_user_moves_on(self):
        from core.news.pipeline import run
        conn = connection('看看新闻')
        original = conn.news_bulletin
        async def delayed(*args):
            conn.dialogue.put(Message(role='user', content='教我Python'))
            return [{'source': '抖音', 'title': '迟到话题'}], []
        with patch('core.news.pipeline._hot', side_effect=delayed):
            result = await run(conn, {'sources': ['抖音']})
        self.assertEqual('superseded', result['status'])
        self.assertIs(original, conn.news_bulletin)

    async def test_screenshot_bilibili_followup_does_not_ask_for_permission(self):
        conn = connection('说一说第二条')
        conn.news_bulletin['items'][1].update(source='哔哩哔哩', title='影之刃零 最终预告')
        with patch('core.web_search.service.search_evidence', new_callable=AsyncMock,
                   return_value={'searchStatus': 'ok', 'items': []}) as lookup:
            result = await search(conn, '模型自行生成的查询')
        self.assertEqual('ok', result['status'])
        lookup.assert_awaited_once()
        query = lookup.await_args.args[1]
        self.assertIn('哔哩哔哩', query)
        self.assertIn('影之刃零 最终预告', query)
        self.assertIsNone(conn.web_search_pending)

    async def test_detail_goes_directly_to_tavily_once_with_real_title(self):
        conn = connection()
        evidence = {'searchStatus': 'ok', 'items': [{'title': '查证资料', 'url': 'https://example.com'}]}
        with patch('core.web_search.service.search_evidence', new_callable=AsyncMock,
                   return_value=evidence) as lookup:
            result = await search(conn, '模型猜测的标题')
            repeated = await search(conn, '又猜了一个标题')
        self.assertEqual('ok', result['status'])
        self.assertEqual(result, repeated)
        lookup.assert_awaited_once()
        self.assertIn('第二条真实话题', lookup.await_args.args[1])
        self.assertNotIn('模型猜测', lookup.await_args.args[1])
        self.assertIsNone(conn.web_search_pending)

    async def test_unrelated_question_still_requires_authorization(self):
        conn = connection('今天心情怎么样')
        with patch('core.web_search.service.search_evidence', new_callable=AsyncMock) as lookup:
            result = await search(conn, '随意生成的搜索')
        self.assertEqual('confirmation_required', result['status'])
        lookup.assert_not_awaited()

    async def test_failed_detail_search_does_not_prompt_or_duplicate_requests(self):
        conn = connection()
        with patch('core.web_search.service.search_evidence', new_callable=AsyncMock,
                   side_effect=TimeoutError) as lookup:
            result = await search(conn, '查第二条')
            self.assertEqual(result, await search(conn, '重试第二条'))
        self.assertEqual('failed', result['status'])
        lookup.assert_awaited_once()
