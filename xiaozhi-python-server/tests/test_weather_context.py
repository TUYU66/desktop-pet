"""Forecast boundaries survive background caches and custom system prompts."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from core.utils.prompt_manager import PromptManager
from core.weather.context import FORECAST_HEADER, WEATHER_USAGE, forecast_context


class WeatherContextTests(unittest.TestCase):
    def test_empty_and_already_labelled_forecasts(self):
        self.assertEqual('', forecast_context(None))
        self.assertEqual('', forecast_context(''))
        report = forecast_context('2026-10-05：白天 小雨，夜间 晴')
        self.assertTrue(report.startswith(FORECAST_HEADER))
        self.assertEqual(report, forecast_context(report))

    def test_legacy_city_cache_cannot_be_used_as_current_weather(self):
        manager = object.__new__(PromptManager)
        manager.config = {'plugins': {'get_weather': {'default_location': '厦门'}}}
        manager.CacheType = SimpleNamespace(WEATHER='weather', LOCATION='location', DEVICE_PROMPT='prompt')
        manager.cache_manager = Mock()
        manager.cache_manager.get.side_effect = lambda kind, key: '2026-10-05：白天 小雨，夜间 晴' if (kind, key) == ('weather', '厦门') else None
        manager.logger = Mock()
        # Loading background context must not trigger a new weather request on a cache hit.
        text = manager._get_weather_info(SimpleNamespace(config=manager.config), '其他城市')
        self.assertTrue(text.startswith(FORECAST_HEADER))
        manager.cache_manager.set.assert_not_called()
        manager.base_prompt_template = '{{base_prompt}}\n{{weather_info}}'
        manager.context_data = ''
        manager._get_current_time_info = Mock(return_value=('2026-10-05', '星期一', ''))
        prompt = manager.build_enhanced_prompt('你是小兰', 'device')
        self.assertIn(FORECAST_HEADER, prompt)
        self.assertIn('白天 小雨，夜间 晴', prompt)

    def test_weather_rules_are_added_even_when_voice_style_already_exists(self):
        # Use the actual method without initializing a connection or starting services.
        source = Path(__file__).resolve().parents[1] / 'core' / 'transport' / 'connection.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        handler = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ConnectionHandler')
        method = next(node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name == 'change_system_prompt')
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        conn = SimpleNamespace(dialogue=SimpleNamespace(update_system_message=Mock()))
        change_prompt = namespace['change_system_prompt']
        change_prompt(conn, '你是小兰。\n[对话表达]\n自然聊天。')
        self.assertIn(WEATHER_USAGE, conn.prompt)
        conn.dialogue.update_system_message.assert_called_with(conn.prompt)
        change_prompt(conn, conn.prompt)
        self.assertEqual(1, conn.prompt.count('[天气信息使用规则]'))


if __name__ == '__main__':
    unittest.main()
