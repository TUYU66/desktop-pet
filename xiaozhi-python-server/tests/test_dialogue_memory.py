"""记忆来源与提示路径回归；不使用真实模型、服务或设备。"""
import unittest

from core.utils.dialogue import Dialogue, Message, saved_memory_recall_answer
from core.providers.memory.mem_local_short.mem_local_short import format_memories


class DialogueMemoryTests(unittest.TestCase):
    def make_dialogue(self, prompt=None):
        dialogue = Dialogue()
        if prompt is not None:
            dialogue.put(Message(role="system", content=prompt))
        dialogue.put(Message(role="user", content="你记住了我什么"))
        return dialogue

    def test_memory_is_injected_with_or_without_template_tags(self):
        for prompt in (None, "你是小智", "<identity>你是小智</identity><context>天气</context>",
                       "<memory>旧的记忆</memory><context>天气</context>"):
            with self.subTest(prompt=prompt):
                dialogue = self.make_dialogue(prompt)
                messages = dialogue.get_llm_dialogue_with_memory("用户养了两只猫", {})
                system = "\n".join(m["content"] for m in messages if m["role"] == "system")
                self.assertIn("用户养了两只猫", system)
                self.assertIn("不是用户", system)
                self.assertIn("助手旧回复", system)
                self.assertNotIn("旧的记忆", system)
                self.assertEqual("你记住了我什么", messages[-1]["content"])

    def test_snapshot_replacement_does_not_mutate_or_cache_prompt(self):
        dialogue = self.make_dialogue("角色背景<context>上下文</context><memory>旧的记忆</memory>")
        first = dialogue.get_llm_dialogue_with_memory("用户喜欢爵士乐", {})
        second = dialogue.get_llm_dialogue_with_memory("", {})
        self.assertIn("用户喜欢爵士乐", str(first))
        self.assertNotIn("用户喜欢爵士乐", str(second))
        self.assertIn("没有可确认", str(second))
        self.assertIn("旧的记忆", dialogue.dialogue[0].content)

    def test_memory_data_with_backslashes_is_not_regex_replacement(self):
        messages = self.make_dialogue("<context>x</context><memory></memory>").get_llm_dialogue_with_memory(r"用户项目位于 D:\new\1", {})
        self.assertIn(r"D:\new\1", str(messages[-2]["content"]))

    def test_direct_recall_only_uses_saved_records(self):
        snapshot = format_memories([{"content": "用户养了两只猫"}])
        for query in ("你记住了我什么？", "你还记得我什么", "我的长期记忆有哪些", "查看我的长期记忆"):
            answer = saved_memory_recall_answer(query, snapshot)
            self.assertIn("用户养了两只猫", answer)
            self.assertNotIn("编程书", answer)
            self.assertNotIn("21岁", answer)

    def test_voiceprint_wrapped_recall(self):
        answer = saved_memory_recall_answer('{"speaker":"小王","content":"你记住了我什么"}', "- 用户目前是大学生")
        self.assertIn("大学生", answer)

    def test_recall_distinguishes_empty_and_unavailable(self):
        self.assertIn("还没有存好", saved_memory_recall_answer("你记住了我什么", ""))
        for snapshot in (None, "长期记忆数据库本轮读取失败，保存情况未知"):
            answer = saved_memory_recall_answer("你记住了我什么", snapshot)
            self.assertIn("没读到", answer)
            self.assertNotIn("还没有存好", answer)

    def test_other_or_compound_questions_use_normal_chat(self):
        for query in ("你是谁", "记住我是大学生", "你记住了我什么，顺便帮我放音乐", "我21岁，是大学生"):
            self.assertIsNone(saved_memory_recall_answer(query, "- 用户养了两只猫"))

