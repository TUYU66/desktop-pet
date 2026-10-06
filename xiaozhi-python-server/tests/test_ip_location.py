import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
from core.utils import ip_location
from core.utils.cache.manager import cache_manager
from plugins_func.functions.get_weather import resolve_location


class IpLocationTests(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()
        self.cached = {}
        self.get_cache = patch.object(cache_manager, "get", side_effect=lambda kind, key: self.cached.get(key))
        self.set_cache = patch.object(cache_manager, "set", side_effect=lambda kind, key, value, **kw: self.cached.__setitem__(key, value))
        self.get_cache.start()
        self.set_cache.start()
        self.addCleanup(self.get_cache.stop)
        self.addCleanup(self.set_cache.stop)

    @patch.object(ip_location.requests, "get")
    def test_non_public_never_queries_server_location(self, get):
        for ip in (None, "", "bad", "192.168.1.2", "10.0.0.2", "127.0.0.1", "100.64.1.1", "::1", "fe80::1", "::ffff:192.168.1.2", "224.0.0.1"):
            self.assertEqual(ip_location.get_ip_info(ip, self.logger), {})
        get.assert_not_called()

    @patch.object(ip_location.requests, "get")
    def test_lookup_cache_and_device_isolation(self, get):
        get.return_value = SimpleNamespace(status_code=200, json=lambda: {"city": "杭州市"})
        self.assertEqual(ip_location.get_ip_info("8.8.8.8", self.logger), {"city": "杭州市"})
        ip_location.get_ip_info("8.8.8.8", self.logger)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs["params"]["ip"], "8.8.8.8")
        self.assertIn("timeout", get.call_args.kwargs)
        ip_location.get_ip_info("1.1.1.1", self.logger)
        self.assertEqual(get.call_count, 2)

    @patch.object(ip_location.requests, "get")
    def test_failure_negative_cache(self, get):
        get.side_effect = requests.Timeout()
        self.assertEqual(ip_location.get_ip_info("8.8.8.8", self.logger), {})
        self.assertEqual(ip_location.get_ip_info("8.8.8.8", self.logger), {})
        self.assertEqual(get.call_count, 1)

    @patch.object(ip_location.requests, "get")
    def test_bad_provider_data(self, get):
        for data in ({}, {"city": None}, {"city": " "}, {"city": "未知位置"}, []):
            self.cached.clear()
            get.return_value = SimpleNamespace(status_code=200, json=lambda: data)
            self.assertEqual(ip_location.get_ip_info("8.8.8.8", self.logger), {})

    @patch.object(ip_location.requests, "get")
    def test_resolution_precedence(self, lookup):
        conn = SimpleNamespace(client_ip="8.8.8.8")
        config = {"default_location": "广州", "auto_location": True}
        self.assertEqual(resolve_location(conn, config, "上海")[0], "上海")
        self.assertEqual(resolve_location(conn, config)[0], "广州")
        self.assertEqual(resolve_location(conn, {})[0], "")
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
