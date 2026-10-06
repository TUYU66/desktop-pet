"""Current platform lists only."""
from .catalog import CHANNEL_MAP, CATEGORY_SOURCES, SOURCE_ALIASES
TOOL_DESCRIPTION = {
 'type': 'function', 'function': {
  'name': 'get_news_from_newsnow',
  'description': '仅获取当前平台热榜或实时综合热点，不是关键词搜索。只有用户需要当前榜单数据时调用。常见梗含义、人物、游戏知识、事件背景优先用自身知识和现有上下文回答；追问刚才某条时优先复用上下文，不因最近/热梗/怎么回事等词重复联网。没有把握就说明不确定并询问是否查证，用户同意后才使用独立web_search。本工具不提供圈内关键词搜索、事件详情或自动搜索降级。',
  'parameters': {'type':'object','additionalProperties':False,'properties': {
   'category': {'type':'string','enum':['general',*CATEGORY_SOURCES],'description':'用户要看的榜单类别，默认general。'},
   'sources': {'type':'array','minItems':1,'maxItems':4,'items':{'type':'string','enum':list(CHANNEL_MAP)+list(SOURCE_ALIASES)},'description':'用户要看的平台，最多四个；省略时使用类别默认榜单。'},
   'count': {'type':'integer','minimum':1,'maximum':5,'description':'返回条数，默认3。'}
  }}
 }
}
