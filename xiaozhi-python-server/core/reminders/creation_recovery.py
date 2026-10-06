"""A refusal permits corrections only when no earlier attempt is unresolved."""
import re


def correction_source(pending, text):
    # A new authorization must give its own date; never inherit a rejected date.
    from core.reminders.batch import DATE_TOKEN
    previous = pending.get('correctionSource', '')
    from core.reminders.parser import NUM
    clock = rf'{NUM}(?:点|时|:|：)(?:半|一刻|三刻|{NUM}分?)?'
    period = r'凌晨|早上|上午|中午|下午|傍晚|晚上|晚间'
    relative_time = rf'(?:{NUM}|半)\s*(?:个小时|小时|分钟|秒钟?)\s*(?:以?后|后再)'
    if re.search(relative_time, text):
        previous = ''  # A new delay is relative to the new server clock.
    else:
        if DATE_TOKEN.search(text): previous = DATE_TOKEN.sub('', previous)
        if re.search(clock, text):
            previous = re.sub(clock, '', previous)
            previous = re.sub(relative_time, '', previous)
        if re.search(period, text): previous = re.sub(period, '', previous)
    source = previous+'，'+text
    dated = bool(DATE_TOKEN.search(source) or re.search(r'(?:下|本|这)?(?:周|星期)[一二三四五六日天]', source))
    relative = bool(re.search(r'(?:秒|分钟|小时).*(?:后|以后)', source))
    periodic = bool(re.search(r'每天|每周|每星期|工作日', source))
    return source, dated or relative or periodic


def rejected_field(draft, text):
    from core.reminders.batch import temporal_answer
    if not temporal_answer(text): return False
    if draft.get('batch'):
        field = draft.get('field')
        return field is not None and bool(draft['batch'][field].get('rejected'))
    return bool(draft.get('rejected'))


def locked_refusal(exc, uncertain):
    if exc.code == 409:
        return '这条提醒可能已经存过了，我还没确认。你先在日程页面看一下，也可以说“重试”让我查清楚，避免重复设置。'
    if uncertain:
        return '刚才那条有没有存好，我还没确认，这次也没查到。你先在日程页面看一下，或者说“重试”让我再查，先别重新设置。'
    return None


def rejected_text(code):
    return '这条没有存好。你重新告诉我日期和时间吧；想按刚才的安排再试一次，就说“重试”，不设了也可以告诉我。'
