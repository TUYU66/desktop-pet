import unittest

from core.utils.dialogue import Dialogue, Message
from core.utils.short_context import summarize_context


class ShortContextTests(unittest.TestCase):
    def make(self, count):
        d = Dialogue()
        d.put(Message('system', 'unchanged'))
        d.put(Message('assistant', 'example', is_temporary=True))
        for i in range(count):
            d.put(Message('user' if i % 2 == 0 else 'assistant', str(i)))
        return d

    def test_threshold_and_rolling_merge(self):
        d = self.make(30)
        calls = []
        def summarize(previous, older):
            calls.append((previous, [m.content for m in older]))
            return 'summary ' + str(len(calls))
        self.assertEqual(d.compact(summarize), 0)
        for i in range(2):
            d.put(Message('user', 'new'))
            d.put(Message('assistant', 'reply'))
            self.assertEqual(d.compact(summarize), 2)
        self.assertEqual(calls, [('', ['0', '1']), ('summary 1', ['2', '3'])])
        self.assertEqual(len(d.dialogue), 32)
        prompt = d.get_llm_dialogue()
        self.assertEqual(sum('summary 2' in (m.get('content') or '') for m in prompt), 1)
        self.assertEqual(d.dialogue[0].content, 'unchanged')

    def test_failure_preserves_original(self):
        d = self.make(32)
        original = list(d.dialogue)
        def fail(*args):
            raise RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            d.compact(fail)
        self.assertEqual(d.dialogue, original)
        self.assertEqual(d.context_summary, '')
        with self.assertRaises(ValueError):
            d.compact(lambda *_: '')
        self.assertEqual(d.dialogue, original)

    def test_tool_boundary(self):
        d = Dialogue()
        d.put(Message('user', 'request'))
        d.put(Message('assistant', tool_calls=[{'id': 'tool1'}]))
        d.put(Message('tool', 'done', tool_call_id='tool1'))
        d.put(Message('assistant', 'result'))
        for m in self.make(28).dialogue[2:]:
            d.put(m)
        self.assertEqual(d.compact(lambda *_: 'tool turn summarized'), 4)
        self.assertEqual(d.dialogue[0].role, 'user')
        self.assertFalse(any(m.role == 'tool' for m in d.dialogue))

    def test_append_during_summary_is_not_lost(self):
        d = self.make(32)
        def summarize(*args):
            d.put(Message('user', 'arrived meanwhile'))
            return 'summary'
        d.compact(summarize)
        self.assertEqual(d.dialogue[-1].content, 'arrived meanwhile')

    def test_new_session_has_no_summary(self):
        d = self.make(32)
        d.compact(lambda *_: 'old session')
        self.assertEqual(Dialogue().context_summary, '')

    def test_summary_model_request_and_empty_result(self):
        class Model:
            def response(self, session, messages, **kwargs):
                self.messages = messages
                return iter(['摘要', '内容'])
        model = Model()
        self.assertEqual(summarize_context(model, 'session', 'previous', [Message('user', 'hello')]), '摘要内容')
        self.assertIn('previous', model.messages[1]['content'])
        self.assertIn('hello', model.messages[1]['content'])


if __name__ == '__main__':
    unittest.main()
