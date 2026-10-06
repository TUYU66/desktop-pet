"""Keep daily forecasts distinct from observed weather in every chat path."""

WEATHER_USAGE = '''[天气信息使用规则]
get_weather 和天气背景提供的是逐日预报，不是实时观测。回答时保留“预报、预计、可能”等预测语义，例如“预报说厦门今天有雨，出门带把伞”。不能据此说“厦门下雨了、外面正在下雨、雨已经停了”，也不能把晴天预报说成现在已经放晴。
用户问“现在下雨了吗”时，如果只有预报，就自然说明“预报今天有雨，不过现在有没有下，我还不能确认”。只有确有对应地点、时间的实时观测或用户提供的现场情况，才能描述当前天气；用户说还没下雨时，不用预报反驳现场情况。
分清白天与夜间、今天与其他日期，不把一天的预报说成全天持续或已经发生。没有返回的降雨时段、概率及日期不能编造；气温范围也是预测。天气数据缺失或查询失败时直说没有查到。
'''

FORECAST_HEADER = (
    '数据类型：逐日天气预报（预测，不是当前实况）\n'
    '当前实况：未查询实时观测，不能据此确认现在是否下雨。\n'
)


def forecast_context(report):
    """Also label legacy background cache entries; leave missing data empty."""
    if not report:
        return ''
    text = str(report)
    return text if text.startswith(FORECAST_HEADER) else FORECAST_HEADER + text
