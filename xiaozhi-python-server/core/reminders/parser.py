"""常见中文日程的确定性时间校验。模糊时间返回追问，绝不猜小时/年份。"""
import re
from datetime import datetime, timedelta, timezone

ZONE=timezone(timedelta(hours=8))
NUM=r"[0-9零〇一二两三四五六七八九十百]+"


def number(value):
    if value.isdigit(): return int(value)
    digits=dict(zip('零〇一二两三四五六七八九',[0,0,1,2,2,3,4,5,6,7,8,9]))
    total=part=0
    for char in value:
        if char in digits: part=digits[char]
        elif char in '十百': total+=(part or 1)*(10 if char=='十' else 100); part=0
        else: raise ValueError('数字无效')
    return total+part


def expand_day(text,now_ms,date_context=None):
    """Resolve a day without a month, reporting the full date to the caller."""
    if re.search(rf'{NUM}月{NUM}[日号]',text): return text,None
    match=re.search(rf'(?:(下个月|下月|本月|这个月)\s*)?({NUM})[日号]',text)
    if not match: return text,None
    if re.search(r'\d{4}年',text):
        return text,'请补充月份，明确是哪年哪月哪号。'
    now=datetime.fromtimestamp(now_ms/1000,ZONE)
    anchor=datetime.fromtimestamp((date_context or now_ms)/1000,ZONE)
    year,month=(now.year,now.month) if match[1] else (anchor.year,anchor.month)
    if match[1] in ('下个月','下月'):
        year,month=(year+1,1) if month==12 else (year,month+1)
    try:
        day=number(match[2])
        chosen=datetime(year,month,day,tzinfo=ZONE).date()
        # Bare “3号” on September 30 means the upcoming October 3.
        # Explicit “本月3号” stays in this month and is rejected as past.
        if not match[1] and chosen<anchor.date():
            year,month=(year+1,1) if month==12 else (year,month+1)
            chosen=datetime(year,month,day,tzinfo=ZONE).date()
    except ValueError:
        return text,'这个日期不存在，请带上月份重新告诉我日期和时间。'
    relative=re.search(r'大后天|后天|明天|今天',text)
    if relative:
        offset={'大后天':3,'后天':2,'明天':1,'今天':0}[relative[0]]
        if (now+timedelta(days=offset)).date()!=chosen:
            return text,'日期说法不一致，请明确是哪月哪号。'
    full=f'{chosen.year}年{chosen.month}月{chosen.day}日'
    return text[:match.start()]+full+text[match.end():],None


def parse_time(text,now_ms,date_context=None):
    text,date_question=expand_day(text,now_ms,date_context)
    if date_question: return None,'once',date_question
    now=datetime.fromtimestamp(now_ms/1000,ZONE)
    rule='once'
    if '每天' in text: rule='daily'
    elif '工作日' in text or '周一至周五' in text: rule='weekdays'
    elif '每周' in text or '每星期' in text: rule='weekly'
    selected=re.search(r'(?:每周|每星期)((?:(?:星期|周)?[一二三四五六日天][、，,和及\s]*)+)',text)
    selected_days=[]
    if selected:
        selected_days=sorted(set(min('一二三四五六日天'.index(day)+1,7) for day in re.findall(r'[一二三四五六日天]',selected[1])))
        if len(selected_days)>1: rule='weekly:'+','.join(map(str,selected_days))
    relative=re.search(rf'({NUM}|半)\s*(秒钟?|分钟|小时|个小时)\s*(?:以?后|后再)',text)
    if relative:
        amount=0.5 if relative[1]=='半' else number(relative[1])
        seconds=amount*(3600 if '小时' in relative[2] else 60 if relative[2]=='分钟' else 1)
        if rule!='once': return None,rule,'周期提醒需要每天或每周的具体几点。'
        if not 10<=seconds<=604800: return None,rule,'请设置10秒到7天以内的延后时长。'
        return int(now_ms+seconds*1000),rule,None
    # 日期：只接受确实存在的日期；省略年份但已过去时追问，不默默跳到明年。
    date=now.date(); explicit_date=False
    dates=list(re.finditer(rf'(?:(\d{{4}})年)?({NUM})月({NUM})[日号]',text))
    m=dates[-1] if dates else None
    try:
        if m:
            date=datetime(int(m[1] or now.year),number(m[2]),number(m[3]),tzinfo=ZONE).date(); explicit_date=True
        else:
            relative_days=list(re.finditer(r'大后天|后天|明天|今天',text))
            if relative_days:
                days={'大后天':3,'后天':2,'明天':1,'今天':0}[relative_days[-1][0]]
                date=(now+timedelta(days=days)).date(); explicit_date=True
            weekday=re.search(r'(每|下|本|这)?(?:周|星期)([一二三四五六日天])',text)
            if weekday and not rule.startswith("weekly:"):
                day='一二三四五六日天'.index(weekday[2]); day=min(day,6)
                offset=day-now.weekday()
                if weekday[1]=='下': offset+=7
                elif weekday[1] not in ('本','这') and offset<0: offset+=7
                date=(now+timedelta(days=offset)).date(); explicit_date=True
    except ValueError:
        return None,rule,'这个日期不存在，请重新告诉我日期和时间。'
    times=list(re.finditer(rf'(?:(凌晨|早上|上午|中午|下午|傍晚|晚上|晚间)\s*)?({NUM})(?:点|时|:|：)(?:(半|一刻|三刻)|({NUM})(?:分)?)?',text))
    if not times: return None,rule,'具体几点提醒你？比如“下午三点”。'
    if len(times)>1 and re.search(r'和|或者|还是|到|至|、',text): return None,rule,'这次先设置哪个具体时间？请一次安排一个时间。'
    match=times[-1]; period=match[1]; hour=number(match[2]); minute=0
    if not period:
        periods=re.findall(r'凌晨|早上|上午|中午|下午|傍晚|晚上|晚间',text)
        if periods: period=periods[-1]
    if match[3]: minute={'半':30,'一刻':15,'三刻':45}[match[3]]
    elif match[4]: minute=number(match[4])
    if not period and 1<=hour<=12:
        # 24小时数字冒号形式明确；口语“三点”需要上午/下午。
        if ':' not in match[0] and '：' not in match[0]: return None,rule,'你说的是上午还是下午？请说完整时间，比如“下午三点”。'
    if period in ('下午','傍晚','晚上','晚间') and 1<=hour<12: hour+=12
    if period=='中午' and 1<=hour<=2: hour+=12
    if period=='凌晨' and hour==12: hour=0
    if hour>23 or minute>59: return None,rule,'时间无效，请告诉我准确的几点几分。'
    target=datetime.combine(date,datetime.min.time(),ZONE).replace(hour=hour,minute=minute)
    if rule in ('daily','weekdays'):
        if target<=now: target+=timedelta(days=1)
        while rule=='weekdays' and target.weekday()>4: target+=timedelta(days=1)
    elif rule.startswith('weekly:'):
        while target<=now or target.isoweekday() not in selected_days: target+=timedelta(days=1)
    elif rule=='weekly':
        if not re.search(r'(?:周|星期)[一二三四五六日天]',text): return None,rule,'每周星期几提醒你？'
        if target<=now: target+=timedelta(days=7)
    elif target<=now:
        return None,rule,'这个时间已经过去了，请明确新的日期和时间。'
    if (target-now).days>5*366: return None,rule,'请设置未来5年内的提醒。'
    return int(target.timestamp()*1000),rule,None


def title_from(text):
    match=re.search(r'(?:提醒我|叫我)(.+)',text)
    if not match: return ''
    title=match[1].strip(' ，。！!')
    # 仅剥离“提醒我”后紧接的时间前缀，作品名和事项内部的日期保持原样。
    prefixes=[rf'({NUM}|半)(?:分钟|小时|个小时|秒钟?)(?:以?后)',
              rf'(?:(\d{{4}})年)?{NUM}月{NUM}[日号]',
              rf'(?:下个月|下月|本月|这个月)?\s*{NUM}[日号]',
              r'(?:每周|每星期)(?:[一二三四五六日天](?:[、，,和及](?:周|星期)?[一二三四五六日天])+)\s*',
              r'每天|每个工作日|工作日|(?:每|下|本|这)?(?:周|星期)[一二三四五六日天]|大后天|后天|明天|今天',
              rf'(凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*{NUM}(?:点|时|:|：)(半|一刻|三刻|{NUM}分?)?']
    for _ in range(5):
        before=title
        for prefix in prefixes: title=re.sub(r'^(?:在)?(?:'+prefix+r')\s*','',title)
        if title==before: break
    return title.strip(' ，。！!')[:120]


def reference_date(text, now):
    current=datetime.fromtimestamp(now/1000,ZONE)
    dates=list(re.finditer(rf'(?:(\d{{4}})年)?({NUM})月({NUM})[日号]',text))
    explicit=dates[-1] if dates else None
    if explicit: return datetime(int(explicit[1] or current.year),number(explicit[2]),number(explicit[3]),tzinfo=ZONE).date().isoformat()
    expanded,question=expand_day(text,now)
    if not question and expanded!=text: return reference_date(expanded,now)
    days=list(re.finditer(r'大后天|后天|明天|今天|昨天',text))
    day=days[-1] if days else None
    if day: return (current+timedelta(days={'昨天':-1,'今天':0,'明天':1,'后天':2,'大后天':3}[day[0]])).date().isoformat()
    weekdays=list(re.finditer(r'(下|本|这)?(?:周|星期)([一二三四五六日天])',text))
    weekday=weekdays[-1] if weekdays else None
    if weekday:
        index=min('一二三四五六日天'.index(weekday[2]),6)
        offset=index-current.weekday()
        if weekday[1]=='下': offset+=7
        elif weekday[1] not in ('本','这') and offset<0: offset+=7
        return (current+timedelta(days=offset)).date().isoformat()
    return None
