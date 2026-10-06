"""QWeather GeoAPI + daily forecast. Credentials stay in request headers."""
import hashlib
import json
import re
from datetime import datetime, timezone

import requests
from config.logger import setup_logging
from core.weather.context import forecast_context
from plugins_func.register import register_function, ToolType, ActionResponse, Action

logger = setup_logging()
TAG = __name__
GET_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市或默认城市的逐天天气预报。返回具体日期、天气和温度范围；仅回答返回日期内的预报，不将预报当作实时观测。",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "城市/区县名称，可附上级行政区以区分同名地点；省略则使用配置的默认城市"},
                "lang": {"type": "string", "description": "语言代码，默认 zh；兼容 zh_CN、zh_HK、en_US"},
            },
            "required": [],
        },
    },
}


class WeatherError(Exception):
    pass


def _host(value):
    host = str(value or "").strip().removeprefix("https://").rstrip("/")
    if not re.fullmatch(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.qweatherapi\.com", host):
        raise WeatherError("请在天气配置中填写控制台分配的 API Host（域名，不含接口路径）")
    return host


def _request(host, key, path, params):
    try:
        response = requests.get(
            f"https://{host}{path}", params=params,
            headers={"X-QW-Api-Key": key}, timeout=(3, 5), allow_redirects=False,
        )
        status = response.status_code
        if status != 200:
            raise WeatherError(_error_message(str(status)))
        data = response.json()
    except requests.Timeout:
        raise WeatherError("天气服务请求超时，请稍后重试") from None
    except requests.RequestException:
        raise WeatherError("天气服务连接失败，请检查网络") from None
    except ValueError:
        raise WeatherError("天气服务返回了无法解析的数据") from None
    if not isinstance(data, dict):
        raise WeatherError("天气服务返回格式异常")
    code = str(data.get("code", "200"))
    if code != "200" or data.get("error"):
        raise WeatherError(_error_message(code))
    return data


def _error_message(code):
    return {
        "204": "未查询到该地点的数据，请提供更明确的城市或区县",
        "400": "天气请求参数无效，请检查地点和预报配置",
        "401": "天气服务鉴权失败，请检查 API Key 与 API Host 是否匹配",
        "402": "天气服务额度或余额不足，请检查和风控制台",
        "403": "天气接口访问被拒绝，请检查凭据权限和已开通的接口",
        "404": "未找到地点或接口，请检查城市名称及预报版本配置",
        "429": "天气请求过于频繁或额度已用尽，请稍后重试并检查控制台",
    }.get(code, "天气服务暂时不可用，请检查接口状态")


def _quantity(item):
    if not isinstance(item, dict) or item.get("value") is None:
        return "未知"
    return f"{item['value']}{item.get('unit', '')}"


def _forecast(config, city, host, key, lang, days):
    version = config.get("forecast_api", "v1")
    if version == "v7":
        if days not in (3, 7, 10, 15, 30):
            raise WeatherError("v7 的 forecast_days 必须为 3、7、10、15 或 30")
        data = _request(host, key, f"/v7/weather/{days}d", {"location": city["id"], "lang": lang, "unit": "m"})
        rows = data.get("daily")
        if not isinstance(rows, list) or not rows:
            raise WeatherError("天气服务没有返回预报数据")
        lines = [f"{r['fxDate']}：白天预报 {r['textDay']}，夜间预报 {r['textNight']}，预计气温 {r['tempMin']}～{r['tempMax']}℃" for r in rows]
        return lines, data.get("updateTime", "未知")
    if version != "v1" or not 1 <= days <= 10:
        raise WeatherError("forecast_api 应为 v1 或 v7；v1 的 forecast_days 范围为 1～10")
    lat, lon = float(city["lat"]), float(city["lon"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise WeatherError("城市查询返回的坐标无效")
    data = _request(host, key, f"/weather/v1/daily/{lat}/{lon}", {"days": days, "localTime": "true", "lang": lang})
    rows = data.get("days")
    if not isinstance(rows, list) or not rows:
        raise WeatherError("天气服务没有返回预报数据")
    lines = []
    for row in rows:
        day, night = row["daytime"], row["nighttime"]
        # The full period can start on the preceding evening; use local daytime date.
        date = day["forecastStartTime"][:10]
        lines.append(f"{date}：白天预报 {day['condition']['text']}，夜间预报 {night['condition']['text']}，预计气温 {_quantity(row.get('temperatureMin'))}～{_quantity(row.get('temperatureMax'))}")
    return lines, None


def resolve_location(conn, config, location=None):
    explicit = str(location or "").strip()
    if explicit:
        return explicit, "用户指定"
    return str(config.get("default_location") or "").strip(), "默认城市"



@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def get_weather(conn, location=None, lang="zh_CN"):
    from core.utils.cache.manager import cache_manager, CacheType

    config = conn.config.get("plugins", {}).get("get_weather", {})
    try:
        key = str(config.get("api_key") or "").strip()
        geo_key = str(config.get("geo_api_key") or key).strip()
        if not key or not geo_key:
            raise WeatherError("天气插件尚未配置 API Key，请先填写天气配置")
        host = _host(config.get("api_host"))
        geo_host = _host(config.get("geo_api_host") or host)
        location, location_source = resolve_location(conn, config, location)
        if not location:
            raise WeatherError("请提供城市名称，或配置 default_location")
        lang = {"zh_cn": "zh", "zh_hk": "zh-hant", "zh_tw": "zh-hant", "en_us": "en", "ja_jp": "ja"}.get(str(lang).lower(), str(lang).lower().replace("_", "-"))
        days = int(config.get("forecast_days", 7))
        ttl = max(0, min(int(config.get("cache_seconds", 600)), 3600))
        fingerprint = json.dumps([host, key, geo_host, geo_key, location, location_source, lang, days, config.get("forecast_api", "v1")], ensure_ascii=False)
        cache_key = "qweather_daily_v2_" + hashlib.sha256(fingerprint.encode()).hexdigest()
        cached = cache_manager.get(CacheType.WEATHER, cache_key) if ttl else None
        if cached is not None:
            return ActionResponse(Action.REQLLM, forecast_context(cached), None)
        geo = _request(geo_host, geo_key, "/geo/v2/city/lookup", {"location": location, "lang": lang, "number": 3})
        cities = geo.get("location")
        if not isinstance(cities, list) or not cities:
            raise WeatherError("未找到该地点，请提供更明确的城市或区县名称")
        city = cities[0]
        lines, updated = _forecast(config, city, host, key, lang, days)
        name = " / ".join(dict.fromkeys(str(city[k]) for k in ("country", "adm1", "adm2", "name") if city.get(k)))
        fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
        report = f"城市来源：{location_source}\n地点：{name}；时区：{city.get('tz', '以地点当地时间为准')}\n数据来源：和风天气 QWeather\n获取时间（UTC）：{fetched}\n"
        if updated:
            report += f"预报更新时间：{updated}\n"
        report += "逐日预报（日期为当地日期）：\n" + "\n".join(lines)
        report += "\n这是逐日预报，不是实时观测；未返回的日期没有可用数据。"
        report = forecast_context(report)
        if ttl:
            cache_manager.set(CacheType.WEATHER, cache_key, report, ttl=ttl)
        return ActionResponse(Action.REQLLM, report, None)
    except WeatherError as exc:
        message = str(exc)
    except (KeyError, TypeError, ValueError, OverflowError):
        message = "天气配置或接口数据格式异常，请检查配置和接口版本"
    # Do not log request URLs, headers, raw provider errors or credential values.
    logger.bind(tag=TAG).warning(message)
    result = ActionResponse(Action.REQLLM, f"天气查询失败：{message}。本次没有取得天气数据，请勿推测天气。", None)
    result.weather_failed = True
    return result
