CHANNEL_MAP = {
    "V2EX": "v2ex-share",
    "知乎": "zhihu",
    "微博": "weibo",
    "联合早报": "zaobao",
    "酷安": "coolapk",
    "MKTNews": "mktnews-flash",
    "华尔街见闻": "wallstreetcn-quick",
    "36氪": "36kr-quick",
    "抖音": "douyin",
    "虎扑": "hupu",
    "百度贴吧": "tieba",
    "今日头条": "toutiao",
    "IT之家": "ithome",
    "澎湃新闻": "thepaper",
    "卫星通讯社": "sputniknewscn",
    "参考消息": "cankaoxiaoxi",
    "远景论坛": "pcbeta-windows11",
    "财联社": "cls-depth",
    "雪球": "xueqiu-hotstock",
    "格隆汇": "gelonghui",
    "法布财经": "fastbull-express",
    "Solidot": "solidot",
    "Hacker News": "hackernews",
    "Product Hunt": "producthunt",
    "Github": "github-trending-today",
    "哔哩哔哩": "bilibili-hot-search",
    "快手": "kuaishou",
    "靠谱新闻": "kaopu",
    "金十数据": "jin10",
    "百度热搜": "baidu",
    "牛客": "nowcoder",
    "少数派": "sspai",
    "稀土掘金": "juejin",
    "凤凰网": "ifeng",
    "虫部落": "chongbuluo-latest",
}

DEFAULT_NEWS_SOURCES = "澎湃新闻;百度热搜;财联社"
CATEGORY_SOURCES = {
    'community': ['哔哩哔哩', '百度贴吧', '微博', '抖音'],
    'entertainment': ['抖音', '微博', '哔哩哔哩', '百度热搜'],
    'news': ['澎湃新闻', '财联社', '今日头条', '凤凰网'],
    'tech': ['V2EX', 'Github', 'Hacker News', 'IT之家'],
}
SOURCE_ALIASES = {'B站': '哔哩哔哩', 'GitHub': 'Github', '贴吧': '百度贴吧', '掘金': '稀土掘金', '澎湃': '澎湃新闻'}
def _sources(config, category, source, sources):
    if category not in ('general', *CATEGORY_SOURCES):
        raise ValueError('热点类别无效')
    # Some tool-calling models emit a single platform as a string despite the
    # array schema. Normalize only that unambiguous case; keep platform checks.
    if isinstance(sources, str):
        sources = [sources]
    if source and sources:
        raise ValueError('source 与 sources 不能同时指定')
    if sources is not None and (not isinstance(sources, list) or not 1 <= len(sources) <= 4):
        raise ValueError('请选择一到四个热点平台')
    if source:
        names = [source]
    elif sources:
        names = sources
    elif category in CATEGORY_SOURCES:
        names = CATEGORY_SOURCES[category]
    else:
        names = str(config.get('news_sources') or DEFAULT_NEWS_SOURCES).split(';')
    if any(not isinstance(name, str) for name in names):
        raise ValueError('热点平台名称无效')
    names = [SOURCE_ALIASES.get(name.strip(), name.strip()) for name in names if name.strip()]
    if not names:
        raise ValueError('请选择一到四个热点平台')
    if any(name not in CHANNEL_MAP for name in names):
        raise ValueError('存在不支持的热点平台，请选择工具列出的来源')
    return list(dict.fromkeys(names))[:4]


