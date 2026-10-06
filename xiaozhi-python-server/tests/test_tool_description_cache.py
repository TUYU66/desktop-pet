import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from core.providers.tools.unified_tool_manager import ToolManager


def description(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


class ToolDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.manager = ToolManager(SimpleNamespace())
        self.original = description("get_weather")
        self.manager._cached_tools = {"get_weather": SimpleNamespace(description=self.original)}

    def test_mutating_first_and_cached_reads_does_not_pollute_registry(self):
        for _ in range(3):
            tools = self.manager.get_function_descriptions()
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]["function"]["name"], "get_weather")
            tools[0]["function"]["name"] = "changed"
            tools.append(description("direct_answer"))
        self.assertEqual(self.original["function"]["name"], "get_weather")

    def test_real_chat_tool_assembly_across_turns_and_recursion(self):
        # Exercise the actual tool-assembly block without loading audio/model services.
        tree = ast.parse(Path("core/conversation/engine.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConversationEngine")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_chat")
        index = next(i for i, n in enumerate(method.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "functions" for t in n.targets))
        block = ast.Module(body=method.body[index:index+2], type_ignores=[])
        code = compile(ast.fix_missing_locations(block), "chat_tool_assembly", "exec")
        shared = [description("get_weather")]
        conn = SimpleNamespace(intent_type="function_call", func_handler=SimpleNamespace(get_functions=lambda: shared))
        direct = description("direct_answer")
        for depth in (0, 0, 1, 0):
            env = dict(self=conn, copy=copy, depth=depth, force_final_answer=False, DIRECT_ANSWER_TOOL=direct)
            exec(code, env)
            names = [t["function"]["name"] for t in env["functions"]]
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual(names.count("direct_answer"), int(depth == 0))
            self.assertEqual(len(shared), 1)
        shared.append(direct)
        env["depth"] = 0
        exec(code, env)
        self.assertEqual(sum(t["function"]["name"] == "direct_answer" for t in env["functions"]), 1)


if __name__ == "__main__":
    unittest.main()
