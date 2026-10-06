import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
from plugins_func.functions import get_weather as weather

GEO = {"code": "200", "location": [{"id": "test-city", "lat": "30.2", "lon": "120.1", "name": "测试市", "tz": "Asia/Shanghai"}]}
DAILY = {"days": [{"forecastStartTime": "2026-09-19T19:00+08:00", "daytime": {"forecastStartTime": "2026-09-20T07:00+08:00", "condition": {"text": "晴"}}, "nighttime": {"condition": {"text": "阴"}}, "temperatureMin": {"value": 0, "unit": "°C"}, "temperatureMax": {"value": 20, "unit": "°C"}}]}


def response(data, status=200):
    return SimpleNamespace(status_code=status, json=lambda: data)


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.config = {"api_host": "test.qweatherapi.com", "api_key": "weather-secret", "geo_api_key": "geo-secret", "default_location": "测试市", "cache_seconds": 0}
        self.conn = SimpleNamespace(config={"plugins": {"get_weather": self.config}})

    @patch.object(weather.requests, "get")
    def test_v1_dates_coordinates_auth_and_zero(self, get):
        get.side_effect = [response(GEO), response(DAILY)]
        result = weather.get_weather(self.conn)
        self.assertIn("2026-09-20", result.result)
        self.assertNotIn("2026-09-19：", result.result)
        self.assertIn("0°C", result.result)
        calls = get.call_args_list
        self.assertEqual(calls[0].kwargs["headers"]["X-QW-Api-Key"], "geo-secret")
        self.assertEqual(calls[1].kwargs["headers"]["X-QW-Api-Key"], "weather-secret")
        self.assertTrue(calls[1].args[0].endswith("/weather/v1/daily/30.2/120.1"))
        self.assertEqual(calls[0].kwargs["params"]["lang"], "zh")
        self.assertEqual(calls[1].kwargs["params"]["localTime"], "true")
        self.assertNotIn("secret", calls[0].args[0])

    @patch.object(weather.requests, "get")
    def test_missing_key_no_request(self, get):
        self.config["api_key"] = ""
        self.assertTrue(weather.get_weather(self.conn).weather_failed)
        get.assert_not_called()

    @patch.object(weather.requests, "get")
    def test_auth_failure_is_not_city_not_found(self, get):
        get.return_value = response({}, 401)
        result = weather.get_weather(self.conn)
        self.assertTrue(result.weather_failed)
        self.assertIn("鉴权失败", result.result)
        self.assertNotIn("secret", result.result)

    @patch.object(weather.requests, "get")
    def test_timeout_and_bad_json(self, get):
        for failure in (requests.Timeout(), requests.ConnectionError(), ValueError()):
            get.side_effect = failure
            self.assertTrue(weather.get_weather(self.conn).weather_failed)

    @patch.object(weather.requests, "get")
    def test_empty_city_and_empty_forecast(self, get):
        get.side_effect = [response({"code": "200", "location": []})]
        self.assertTrue(weather.get_weather(self.conn).weather_failed)
        get.side_effect = [response(GEO), response({"days": []})]
        self.assertTrue(weather.get_weather(self.conn).weather_failed)

    @patch.object(weather.requests, "get")
    def test_provider_error_codes(self, get):
        for code in ("204", "403", "429", "500"):
            get.side_effect = None
            get.return_value = response({"code": code})
            self.assertTrue(weather.get_weather(self.conn).weather_failed)

    @patch.object(weather.requests, "get")
    def test_v7(self, get):
        self.config["forecast_api"] = "v7"
        get.side_effect = [response(GEO), response({"code": "200", "updateTime": "time", "daily": [{"fxDate": "2026-09-20", "textDay": "晴", "textNight": "阴", "tempMin": "0", "tempMax": "20"}]})]
        self.assertIn("0～20℃", weather.get_weather(self.conn).result)
        self.assertTrue(get.call_args.args[0].endswith("/v7/weather/7d"))

    @patch.object(weather.requests, "get")
    def test_rain_forecasts_never_claim_current_observation(self, get):
        daily = copy.deepcopy(DAILY)
        daily["days"][0]["daytime"]["condition"]["text"] = "小雨"
        daily["days"][0]["nighttime"]["condition"]["text"] = "晴"
        v7 = {"daily": [{"fxDate": "2026-09-20", "textDay": "小雨", "textNight": "晴", "tempMin": "0", "tempMax": "20"}]}
        for version, data in (("v1", daily), ("v7", v7)):
            with self.subTest(version=version):
                self.config["forecast_api"] = version
                get.side_effect = [response(GEO), response(data)]
                text = weather.get_weather(self.conn).result
                self.assertIn("逐日天气预报（预测，不是当前实况）", text)
                self.assertIn("不能据此确认现在是否下雨", text)
                self.assertIn("2026-09-20：白天预报 小雨，夜间预报 晴", text)
                self.assertIn("预计气温", text)
                self.assertNotIn("下雨了", text)
                self.assertNotIn("正在下雨", text)

    @patch.object(weather.requests, "get")
    def test_cache_and_configuration_isolation(self, get):
        from core.utils.cache.manager import cache_manager, CacheType
        self.config["cache_seconds"] = 600
        saved = {}
        with patch.object(cache_manager, "get", side_effect=lambda kind, key: saved.get(key)), patch.object(cache_manager, "set", side_effect=lambda kind, key, value, **kw: saved.__setitem__(key, value)):
            get.side_effect = [response(GEO), response(DAILY), response(GEO), response(DAILY)]
            first = weather.get_weather(self.conn)
            self.assertEqual(first.result, weather.get_weather(self.conn).result)
            self.assertIn("不是当前实况", first.result)
            self.assertEqual(get.call_count, 2)
            self.config["api_key"] = "new-secret"
            weather.get_weather(self.conn)
            self.assertEqual(get.call_count, 4)

    @patch.object(weather.requests, "get")
    def test_reject_non_qweather_host(self, get):
        self.config["api_host"] = "https://example.com/path"
        self.assertTrue(weather.get_weather(self.conn).weather_failed)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
