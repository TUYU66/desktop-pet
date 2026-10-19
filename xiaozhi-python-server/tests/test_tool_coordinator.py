"""Tool streams and uncertain physical outcomes, with no external device calls."""
import unittest
from concurrent.futures import Future
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from core.conversation.cancellation import ChatTurnCancelled
from core.conversation.tool_coordinator import (
    dispatch_tool_calls, extract_direct_answer_response, handle_tool_results, merge_tool_calls,
)
from core.utils.dialogue import Dialogue, Message
import json
from plugins_func.register import Action, ActionResponse


class ToolCoordinatorTests(unittest.TestCase):
    def session(self):
        return NS(client_abort=False, config={}, logger=Mock(), loop=Mock(),
                  func_handler=NS(handle_llm_function_call=Mock(return_value=object())),
                  tts=Mock(), dialogue=Dialogue(), sentence_id='sentence', chat=Mock())

    def call(self, name='self_chassis_turn_left', arguments='{}', identity='call-1'):
        return {'name': name, 'id': identity, 'arguments': arguments}

    def fragment(self, index, name='', arguments='', identity=''):
        return NS(index=index, id=identity, function=NS(name=name, arguments=arguments))

    def test_interleaved_streams_can_arrive_out_of_order(self):
        calls = []
        merge_tool_calls(calls, [self.fragment(1, 'second', '{"b":', 'two')])
        merge_tool_calls(calls, [self.fragment(0, 'first', '{"a":1}', 'one'), self.fragment(1, arguments='2}')])
        self.assertEqual([self.call('first', '{"a":1}', 'one'), self.call('second', '{"b":2}', 'two')], calls)

    def test_invalid_or_unbounded_indices_and_conflicting_ids_are_rejected(self):
        for index in (-1, 32, True, '1'):
            with self.subTest(index=index), self.assertRaises(ValueError):
                merge_tool_calls([], [self.fragment(index)])
        calls = [self.call()]
        with self.assertRaises(ValueError):
            merge_tool_calls(calls, [self.fragment(0, identity='different')])

    def test_direct_answer_accepts_partial_text_but_not_nontext_payload(self):
        self.assertEqual('你好', extract_direct_answer_response('{"response":"你好'))
        for value in ('{"response":null}', '{"response":12}', 12, None):
            self.assertEqual('', extract_direct_answer_response(value))

    def test_whole_batch_is_validated_before_any_tool_is_dispatched(self):
        session = self.session()
        for arguments in ('{"broken":', '[1,2]'):
            with patch('core.conversation.tool_coordinator.asyncio.run_coroutine_threadsafe') as schedule:
                results = dispatch_tool_calls(session, [self.call(), self.call('second', arguments, 'two')])
            schedule.assert_not_called()
            self.assertEqual(Action.ERROR, results[0][0].action)
            self.assertIn('参数有误', results[0][0].result)
        session.func_handler.handle_llm_function_call.assert_not_called()

    def test_duplicate_call_id_rejects_the_entire_batch(self):
        session = self.session()
        with patch('core.conversation.tool_coordinator.asyncio.run_coroutine_threadsafe') as schedule:
            results = dispatch_tool_calls(session, [self.call(), self.call('second')])
        schedule.assert_not_called()
        self.assertEqual(Action.ERROR, results[0][0].action)
        self.assertIn('编号重复', results[0][0].result)
        self.assertNotIn('重新说', results[0][0].result)

    def test_missing_identity_is_distinct_from_duplicate_and_invalid_arguments(self):
        session = self.session()
        with patch('core.conversation.tool_coordinator.asyncio.run_coroutine_threadsafe') as schedule:
            results = dispatch_tool_calls(session, [self.call(identity=None)])
        schedule.assert_not_called()
        self.assertIn('缺少必要', results[0][0].result)

    def test_turn_timeout_is_longer_and_unconfirmed_action_is_never_replayed(self):
        session = self.session()
        pending = Future()
        with patch('core.conversation.tool_coordinator.asyncio.run_coroutine_threadsafe', return_value=pending) as schedule, \
                patch('core.conversation.tool_coordinator.wait_chat_future', side_effect=TimeoutError) as wait, \
                patch('core.conversation.tool_coordinator.enqueue_tool_report'):
            results = dispatch_tool_calls(session, [self.call()])
        self.assertEqual(50, wait.call_args.kwargs['timeout'])
        self.assertEqual(1, schedule.call_count)
        self.assertIn('结果还没确认', results[0][0].result)
        self.assertFalse(pending.cancelled())

    def test_cancelled_conversation_stops_waiting_without_cancelling_physical_work(self):
        session = self.session()
        pending = Future()
        with patch('core.conversation.tool_coordinator.asyncio.run_coroutine_threadsafe', return_value=pending), \
                patch('core.conversation.tool_coordinator.wait_chat_future', side_effect=ChatTurnCancelled), \
                patch('core.conversation.tool_coordinator.chat_cancelled', side_effect=[False, True]), \
                patch('core.conversation.tool_coordinator.enqueue_tool_report'):
            self.assertIsNone(dispatch_tool_calls(session, [self.call()]))
        self.assertFalse(pending.cancelled())

    def test_streamed_tool_reply_is_recorded_without_duplicate_speech(self):
        session = self.session()
        handle_tool_results(session, [(ActionResponse(action=Action.RESPONSE, result='已经完成'), self.call())],
                            depth=0, streamed_text='好的，已经完成')
        session.tts.tts_one_sentence.assert_not_called()
        self.assertEqual('已经完成', session.dialogue.dialogue[-1].content)

    def test_empty_tool_result_still_completes_history_pair_before_llm_continues(self):
        session = self.session()
        handle_tool_results(session, [(ActionResponse(action=Action.REQLLM, result=None), self.call(identity=None))], depth=0)
        assistant, tool = session.dialogue.dialogue[-2:]
        self.assertTrue(assistant.tool_calls[0]['id'])
        self.assertEqual(assistant.tool_calls[0]['id'], tool.tool_call_id)
        self.assertEqual('', tool.content)
        self.assertEqual('', session.dialogue.get_llm_dialogue()[-1]['content'])
        session.chat.assert_called_once_with(None, depth=1)

    def test_orphaned_call_does_not_claim_the_physical_action_was_cancelled(self):
        session = self.session()
        session.dialogue.put(Message(role='assistant', tool_calls=[{
            'id': 'pending', 'type': 'function', 'function': {'name': 'turn', 'arguments': '{}'},
        }]))
        message = session.dialogue.get_llm_dialogue()[-1]
        self.assertEqual('pending', message['tool_call_id'])
        self.assertEqual('unknown', json.loads(message['content'])['status'])
        self.assertNotIn('已取消', message['content'])
