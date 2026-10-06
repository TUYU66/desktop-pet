"""Save explicit multi-reminder requests separately, retaining each receipt."""
import re
import asyncio
import time
import uuid

from core.reminders import client as reminder_client
from core.reminders.parser import NUM, parse_time, title_from
from core.reminders.edits import edit_request, remember, valid_context

MARKER = re.compile(r'提醒我|叫我')
BOUNDARY = re.compile(r'[，,；;。\n]|然后|并且|另外|还有|同时|以及|还要')
RETRY = {'重试', '再试一次', '重试一次', '核实刚才的设置'}
DATE_TOKEN = re.compile(rf'(?:(\d{{4}})年)?{NUM}月{NUM}[日号]|'
    rf'(?:下个月|这个月|下月|本月)?\s*{NUM}[日号]|大后天|后天|明天|今天')


def outside_quotes(text):
    pairs = {'“': '”', '‘': '’', '《': '》', '「': '」', '『': '』', '"': '"'}
    stack, chars = [], []
    for char in text:
        if stack and char == stack[-1]:
            chars.append(' ')
            stack.pop()
        elif char in pairs:
            stack.append(pairs[char])
            chars.append(' ')
        else:
            chars.append(' ' if stack else char)
    return ''.join(chars)


def temporal_answer(text):
    rest = re.sub(rf'一刻|三刻|{NUM}|大后天|后天|明天|今天|下个月|这个月|下月|本月|'
        r'凌晨|早上|上午|中午|下午|傍晚|晚上|晚间|每星期|每周|每天|每个|工作日|星期|周|'
        r'以後|以后|后再|后|小时前|小时|分钟|秒钟|点|时|分|秒|年|月|日|号|半|一刻|三刻|整|'
        r'然后|并且|另外|还有|同时|以及|还要|再|也|就|是|请|帮我|在|到|吧|改到|改成', '', text)
    return not rest.strip(' ，,；;。！!:：、和及至- \n')


def split_requests(text):
    masked = outside_quotes(text)
    markers = list(MARKER.finditer(masked))
    if len(markers) < 2:
        return None
    parts, start = [], 0
    for previous, current in zip(markers, markers[1:]):
        choices = list(BOUNDARY.finditer(masked, previous.end(), current.start()))
        boundary = next((x for x in choices if temporal_answer(masked[x.end():current.start()])), None)
        if boundary is None:
            raise ValueError('我没分清哪件事对应哪个时间，你把每件事和时间分开告诉我吧。')
        parts.append(text[start:boundary.start()].strip(' ，,；;。\n'))
        start = boundary.end()
    parts.append(text[start:].strip(' ，,；;。\n'))
    if len(parts) > 5:
        raise ValueError('一次最多帮你设5条，其他的再分开告诉我吧。')
    return parts


def is_batch_pending(conn):
    return bool(getattr(conn, 'schedule_batch_pending', None))


def keep(conn, pending):
    pending['expires'] = time.monotonic()+300
    conn.schedule_batch_pending = pending


def summary(entries, reference_ms=None):
    from core.conversation.feedback import creation_feedback
    lines = []
    for index, entry in enumerate(entries, 1):
        if not entry.get('receipt'):
            continue
        body, result = entry['body'], entry['receipt']
        feedback = creation_feedback(result, body, reference_ms, include_seconds='秒' in entry.get('source', ''))
        lines.append(f'第{index}条，'+feedback.removeprefix('好，'))
    return ''.join(lines)


from core.reminders.receipts import verified


async def handle(conn, text):
    """Return None for ordinary input; a batch always returns its actual status."""
    pending = getattr(conn, 'schedule_batch_pending', None)
    pending = pending if pending and pending.get('batch') else None
    short = text.strip(' 。！!，,')
    if pending:
        if not valid_context(conn, pending):
            conn.schedule_batch_pending = None
            return ('刚才这组提醒的追问已失效，你先在日程页面看一下已经存了哪些，再告诉我要安排什么吧。'
                    if temporal_answer(text) or MARKER.search(text) or short in RETRY else None)
        if short in ('算了', '不用了', '取消', '取消设置'):
            conn.schedule_batch_pending = None
            saved = summary(pending['batch'])
            suffix = ' 刚才存好的这些还在：'+saved if saved else ''
            if pending.get('retry'):
                suffix += ' 上次那条有没有存好，还得在日程页面看一下。'
            return '好，这组剩下的先不设了。'+suffix
        if pending.get('retry') and short not in RETRY:
            if MARKER.search(text) or temporal_answer(text):
                return '这组里还有提醒没确认存好。你说“重试”让我查一下，或者先在日程页面看一下，避免重复设置。'
            return None
        if (pending.get('field') is not None and not temporal_answer(text)
                and not (short in RETRY and pending['batch'][pending['field']].get('rejected'))):
            if len(list(MARKER.finditer(outside_quotes(text)))) >= 2:
                return '请先补完或取消刚才这组提醒，再设置另一组提醒。'
            return None
    else:
        if edit_request(text) or re.search(r'如果|假如|比如|举例|引用|不要|不需要|别|他说|她说|[？?]', outside_quotes(text)):
            return None
        try:
            parts = split_requests(text)
        except ValueError as exc:
            return str(exc)
        if parts is None:
            return None
        if getattr(conn, 'schedule_edit_pending', None):
            return '请先完成或取消刚才的提醒修改，再设置这组新提醒。'
        pending = dict(action='create', text=text, batch=[dict(source=x) for x in parts],
            session=getattr(conn, 'session_id', None), device=conn.headers.get('device-id', ''))
    device = conn.headers.get('device-id', '')
    if not device:
        return '我还没连上提醒设备，连好后再帮你安排。'
    attempt = None
    try:
        snapshot = await reminder_client.state(device)
        now = snapshot['serverNow']
        pending.setdefault('baseNow', now)
        entries = pending['batch']
        field = pending.pop('field', None)
        if field is not None:
            entry = entries[field]
            if entry.get('rejected'):
                if short in RETRY:
                    entry['body'] = entry['rejectedBody']
                    entry.pop('rejected')
                else:
                    from core.reminders.creation_recovery import correction_source
                    source, complete = correction_source(entry, text)
                    entry['correctionSource'] = source
                    entry['baseNow'] = now
                    pending['field'] = field
                    keep(conn, pending)
                    if not complete:
                        return f'第{field+1}条“{entry["title"]}”没有存好，你再告诉我哪天几点提醒吧。其他存好的提醒还在。'
                    at, rule, question = parse_time(source, now)
                    if question: return f'第{field+1}条“{entry["title"]}”：'+question
                    entry['body'] = dict(action='create', requestId=str(uuid.uuid4()), title=entry['title'],
                                         triggerAt=at, recurrence=rule)
                    entry['timeSource'] = source
                    entry.pop('rejected')
                    pending.pop('field')
                field = None
        if field is not None:
            entry = entries[field]
            if DATE_TOKEN.search(text):
                entry['timeSource'] = DATE_TOKEN.sub('', entry['timeSource'])
            # A new concrete clock replaces the ambiguous clock in the question.
            from core.reminders.edits import CLOCK
            if CLOCK.search(text):
                entry['timeSource'] = re.sub(rf'{NUM}(?:点|时|:|：)(半|一刻|三刻|{NUM}分?)?', '', entry['timeSource'])
            if re.search(r'凌晨|早上|上午|中午|下午|傍晚|晚上|晚间',text):
                entry['timeSource'] = re.sub(r'凌晨|早上|上午|中午|下午|傍晚|晚上|晚间','',entry['timeSource'])
            entry['timeSource'] += '，'+text
        anchor = None
        # Validate all fields before submitting the first operation.
        for index, entry in enumerate(entries):
            if 'body' in entry:
                anchor = entry['body']['triggerAt']
                continue
            if 'title' not in entry:
                entry['title'] = title_from(entry['source'])
                marker = MARKER.search(entry['source'])
                content_at = entry['source'].find(entry['title'], marker.end()) if marker else -1
                entry['timeSource'] = (entry['source'][:content_at]+entry['source'][content_at+len(entry['title']):]
                                       if content_at >= 0 else entry['source'])
            if not entry['title']:
                conn.schedule_batch_pending = None
                return f'第{index+1}条还没说要提醒什么，这组还没有保存。你补上这件事再告诉我吧。'
            temporal = entry['timeSource']
            at, rule, question = parse_time(temporal, pending['baseNow'], anchor)
            if question or at <= now:
                pending['field'] = index
                keep(conn, pending)
                saved = summary(entries, now)
                suffix = ' 刚才存好的这些还在：'+saved+'其余还没设置。' if saved else ' 这组还没有保存。'
                return f'第{index+1}条“{entry["title"]}”：'+(question or '这个时间已经过去了，请重新说明时间。')+suffix
            entry['body'] = dict(action='create', requestId=str(uuid.uuid4()), title=entry['title'],
                                 triggerAt=at, recurrence=rule)
            anchor = at
        keep(conn, pending)
        for index, entry in enumerate(entries):
            if entry.get('receipt'):
                continue
            # Recover an unknown attempt first; only then unlock later expired,
            # unsubmitted entries. Otherwise its field question blocks recovery.
            if not entry.get('uncertain') and entry['body']['triggerAt'] <= now:
                entry['rejected'] = 400
                entry['rejectedBody'] = entry.pop('body')
                entry.pop('correctionSource', None)
                pending['retry'] = False
                pending['field'] = index
                saved = summary(entries, now)
                return f'第{index+1}条“{entry["title"]}”没有存好，时间已经过去了，你再告诉我哪天几点提醒吧。'+(' 刚才存好的这些还在：'+saved if saved else '')
            pending['retry'] = True
            attempt = entry
            result = await reminder_client.command(device, entry['body'])
            if not verified(result, entry['body'], device):
                raise ValueError('batch_create_receipt_mismatch')
            entry['receipt'] = result
            entry.pop('uncertain', None)
            attempt = None
            pending['retry'] = False
            conn.logger.bind(tag=__name__).info('分条提醒已保存: index={}, id={}, triggerAt={}',
                index+1, result['id'], entry['body']['triggerAt'])
        conn.schedule_batch_pending = None
        conn.schedule_operation_complete = True
        last = entries[-1]
        remember(conn, 'item' if last['body']['recurrence'] == 'once' else 'plan',
                 dict(id=last['receipt']['id'],title=last['receipt']['current'].get('title',last['title'])))
        count = {2:'两',3:'三',4:'四',5:'五'}.get(len(entries), str(len(entries)))
        opening = f'好，这{count}条提醒都记好了。' if all(entry['receipt']['current']['status'] in ('scheduled','active') for entry in entries) else '这几条提醒我都找到了。'
        return opening+summary(entries,now)
    except reminder_client.OperationRejected as exc:
        conn.logger.bind(tag=__name__).warning('分条提醒操作被拒绝: code={}, creation={}, previously_unknown={}',
            exc.code,attempt is not None,bool(attempt and attempt.get('uncertain')))
        conn.schedule_recent = None
        keep(conn, pending)
        saved = summary(pending['batch'])
        prefix = '已经确认存好的有：'+saved if saved else '目前还没有确认存好的提醒。'
        if attempt is None:
            return prefix+'这次没查到日程，你在日程页面看一下，过会儿再试吧。'
        from core.reminders.creation_recovery import locked_refusal, rejected_text
        locked = locked_refusal(exc, attempt.get('uncertain'))
        index = pending['batch'].index(attempt)
        suffix = f'后面{len(pending["batch"])-index-1}条还没设置。' if len(pending['batch'])>index+1 else ''
        if locked:
            attempt['uncertain'] = True  # A conflict does not prove absence, even on the first attempt.
            return prefix+f'第{index+1}条：'+locked+suffix
        pending['retry'] = False
        pending['field'] = index
        attempt['rejected'] = exc.code
        attempt['rejectedBody'] = attempt.pop('body')
        attempt.pop('correctionSource', None)
        return prefix+f'第{index+1}条：'+rejected_text(exc.code)+suffix
    except asyncio.CancelledError:
        if attempt is not None: attempt['uncertain'] = True
        raise
    except Exception as exc:
        if attempt is not None: attempt['uncertain'] = True
        conn.schedule_recent = None
        conn.logger.bind(tag=__name__).warning('分条提醒设置未确认: {}', type(exc).__name__)
        keep(conn, pending)
        saved = summary(pending['batch'])
        prefix = '已经确认存好的有：'+saved if saved else '目前还没有确认存好的提醒。'
        if pending.get('retry'):
            index = next(i for i, x in enumerate(pending['batch'], 1) if not x.get('receipt'))
            remaining = len(pending['batch'])-index
            suffix = f'后面{remaining}条还没设置。' if remaining else ''
            return prefix+f'第{index}条有没有存好，我还没确认。'+suffix+'你说“重试”让我查一下，或者先在日程页面看一下吧。'
        return prefix+'这次没查到日程，过会儿再试这组设置吧。'
