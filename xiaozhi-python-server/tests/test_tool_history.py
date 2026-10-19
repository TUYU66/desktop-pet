"""Provider history repair never changes persisted messages or replays a tool."""
import json
import unittest
from core.utils.dialogue import Dialogue, Message


def call(*identities):
    return Message(role='assistant', tool_calls=[{
        'id': identity, 'type': 'function', 'function': {'name': 'test', 'arguments': '{}'},
    } for identity in identities])


class ToolHistoryTests(unittest.TestCase):
    def repair(self, messages):
        return Dialogue()._ensure_tool_calls_complete(messages)

    def test_missing_result_is_inserted_before_next_user_turn_in_call_order(self):
        user = Message(role='user', content='你现在转好了吗？')
        messages = [call('first', 'second'), user]
        repaired = self.repair(messages)
        self.assertEqual(['assistant', 'tool', 'tool', 'user'], [m.role for m in repaired])
        self.assertEqual(['first', 'second'], [m.tool_call_id for m in repaired[1:3]])
        self.assertTrue(all(json.loads(m.content)['status'] == 'unknown' for m in repaired[1:3]))
        self.assertEqual(2, len(messages))
        self.assertIs(user, repaired[-1])

    def test_partial_parallel_results_keep_actual_output_and_fill_only_missing(self):
        actual = Message(role='tool', tool_call_id='second', content='已完成')
        repaired = self.repair([call('first', 'second'), actual])
        self.assertEqual(['first', 'second'], [m.tool_call_id for m in repaired[1:]])
        self.assertEqual('unknown', json.loads(repaired[1].content)['status'])
        self.assertEqual('已完成', repaired[2].content)

    def test_late_known_result_is_paired_and_marked_late_without_changing_original(self):
        original = Message(role='tool', tool_call_id='turn', content='已完成转向')
        user = Message(role='user', content='怎么还没转好？')
        messages = [call('turn'), user, original]
        repaired = self.repair(messages)
        self.assertEqual(['assistant', 'tool', 'user'], [m.role for m in repaired])
        self.assertIn('延迟到达', repaired[1].content)
        self.assertIn('已完成转向', repaired[1].content)
        self.assertEqual('已完成转向', original.content)
        self.assertIs(user, messages[1])

    def test_reused_id_does_not_steal_a_future_round_result(self):
        messages = [call('same'), Message(role='user', content='下一轮'), call('same'),
                    Message(role='tool', tool_call_id='same', content='第二轮结果')]
        repaired = self.repair(messages)
        self.assertEqual('unknown', json.loads(repaired[1].content)['status'])
        self.assertEqual('第二轮结果', repaired[-1].content)

    def test_duplicate_and_orphan_metadata_only_disappear_from_model_view(self):
        assistant = call('one', 'one', None)
        messages = [Message(role='tool', tool_call_id='orphan', content='旧结果'), assistant,
                    Message(role='tool', tool_call_id='one', content='完成'),
                    Message(role='tool', tool_call_id='one', content='重复完成')]
        repaired = self.repair(messages)
        self.assertEqual(2, len(repaired))
        self.assertEqual(1, len(repaired[0].tool_calls))
        self.assertEqual('完成', repaired[1].content)
        self.assertEqual(3, len(assistant.tool_calls))
        self.assertEqual(4, len(messages))

    def test_serialized_history_is_paired_before_interruption_and_is_repeatable(self):
        dialogue = Dialogue()
        dialogue.put(call('pending'))
        dialogue.put(Message(role='user', content='先别回复了'))
        first = dialogue.get_llm_dialogue()
        second = dialogue.get_llm_dialogue()
        self.assertEqual(first, second)
        self.assertEqual(['assistant', 'tool', 'user'], [m['role'] for m in first[-3:]])
