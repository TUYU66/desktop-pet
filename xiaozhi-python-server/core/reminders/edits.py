"""Ground reminder edits in live records; never use a chat reply as a receipt."""
import re
import time
from datetime import datetime

from core.reminders import client as reminder_client
from core.reminders.parser import NUM, ZONE, number, parse_time, expand_day, reference_date

EDIT = re.compile(r'修改|改到|改为|改成|改一下|调整|延后|推迟|推后|往后(?:推|延)|提前|往前推|说错了|刚才错了')
CORRECTION = re.compile(r'说错了|刚才错了|不是.{0,30}是|改成单次|只提醒一次|只要一次')
TIME = re.compile(r'点|时|分钟|秒|今天|明天|后天|月|日|号|周|每天|上午|下午|晚上|中午|凌晨|\d{1,2}[:：]\d{2}')
UNSAFE = re.compile(r'如果|假如|比如|引用|他说|她说|(?:不要|别).{0,20}(?:改|修改|调整|延后|推迟|推后|往后|提前)|[“”"？?]|(?:可以|会|能)改吗')
SHIFT = re.compile(rf'(延后|推迟|推后|往后(?:推|延)|提前|往前推)\s*({NUM}|半)\s*(个小时|小时|分钟|秒钟|秒)')
CLOCK = re.compile(rf'(?:{NUM})(?:点|时|:|：)')
WHOLE = re.compile(r'整个|所有后续|全部|每天|每周|每星期|工作日')
ONCE = re.compile(r'单次|只(?:提醒|要)?一次|不再每天|不是每天|不要周期')
RETRY = {'重试', '再试一次', '重试一次', '核实刚才的修改'}


def edit_request(text):
    return (isinstance(text, str) and not UNSAFE.search(text) and bool(EDIT.search(text) or CORRECTION.search(text))
            and bool(TIME.search(text) or re.search(r'提醒|日程', text)))


def valid_context(conn, value):
    return (value and value.get('expires', 0) > time.monotonic()
            and value.get('session') == getattr(conn, 'session_id', None)
            and value.get('device') == conn.headers.get('device-id', ''))


def remember(conn, kind, item):
    conn.schedule_recent = dict(kind=kind, id=item['id'], title=item['title'],
        session=getattr(conn, 'session_id', None), device=conn.headers.get('device-id', ''),
        expires=time.monotonic()+300)


def title_matches(title, text):
    noun = re.sub(r'^(?:拿|取|做|整理|收拾|打扫|完成|提交|交|喝|吃)', '', title)
    return title in text or len(noun) >= 2 and noun in text


def recent_reference(text):
    if CORRECTION.search(text) or re.search(r'这个|这条|刚才|原来|之前', text):
        return True
    if not re.match(r'^(?:小智[，, ]*|请|帮我)*(?:改到|改成|改为|调整到)', text):
        return False
    remainder = re.sub(r'^(?:小智[，, ]*|请|帮我)*(?:改到|改成|改为|调整到)', '', text)
    remainder = re.sub(rf'{NUM}|今天|明天|后天|凌晨|上午|中午|下午|晚上|每天|每周|工作日|星期|点|时|分|秒|半|月|日|号|提醒我|只|一次|吧|在', '', remainder)
    return not remainder.strip(' ，,。！!:：')


def followup_answer(text):
    """Accept a field/selection answer, not casual speech mentioning tomorrow."""
    if edit_request(text):
        return True
    remainder = re.sub(rf'{NUM}|大后天|后天|明天|今天|凌晨|早上|上午|中午|下午|傍晚|晚上|晚间|'
        r'整个周期|所有后续|全部|工作日|每星期|每周|每天|星期|周|改成单次|单次|这一次|这次|'
        r'提醒我|提醒|日程|小时|分钟|秒钟|时|点|分|秒|半|一刻|三刻|年|月|日|号|'
        r'选|第|条|个|小智|请|帮我|就|是|在|到|吧|只|要|一次', '', text)
    return not remainder.strip(' ，,。！!:：、')


def occurrence(plan, date):
    anchor = datetime.fromtimestamp(plan.get('initialAt', plan['nextAt'])/1000, ZONE)
    at = datetime.combine(datetime.fromisoformat(date).date(), anchor.timetz())
    rule = plan['recurrence']
    matches = (rule == 'daily' or rule == 'weekly' and at.weekday() == anchor.weekday()
        or rule == 'weekdays' and at.weekday() < 5
        or rule.startswith('weekly:') and str(at.isoweekday()) in rule[7:].split(','))
    stamp = int(at.timestamp()*1000)
    return stamp if matches and stamp >= plan['nextAt'] else None


def targets(snapshot, text, whole, date, recent=None):
    items = [dict(x, kind='item') for x in snapshot['items']
             if x['status'] not in ('completed', 'cancelled', 'expired', 'missed', 'dispatching')]
    plans = [dict(x, kind='plan') for x in snapshot['plans'] if x['status'] != 'cancelled']
    values = plans if whole else items+plans
    named = [x for x in values if title_matches(x['title'], text)]
    if recent and CORRECTION.search(text):
        values = [x for x in values if x['kind'] == recent['kind'] and x['id'] == recent['id']
                  and (not named or x in named)]
    elif named:
        values = named
    elif recent and recent_reference(text):
        values = [x for x in values if x['kind'] == recent['kind'] and x['id'] == recent['id']]
    else:
        return []
    if date and not whole and not CORRECTION.search(text):
        selected = []
        for item in values:
            if item['kind'] == 'item':
                times = [item.get('scheduledAt') or item['dueAt']]
                if any(datetime.fromtimestamp(at/1000, ZONE).date().isoformat() == date for at in times):
                    selected.append(item)
            else:
                at = occurrence(item, date)
                if at is not None:
                    if not any(x.get('planId') == item['id'] and (x.get('originalDueAt') or x['dueAt']) == at for x in snapshot['items']):
                        selected.append(dict(item, occurrenceAt=at))
        values = selected
    return values


def save_pending(conn, **fields):
    conn.schedule_edit_pending = dict(fields, session=getattr(conn, 'session_id', None),
        device=conn.headers.get('device-id', ''), expires=time.monotonic()+300)


def same_time(item, body):
    return (item.get('title') == body.get('title') and item.get('dueAt') == body['triggerAt'])


async def submit(conn, body, mode, source, retry=False):
    device = conn.headers['device-id']
    if retry:
        snapshot = await reminder_client.state(device)
        if mode == 'plan':
            current = next((p for p in snapshot['plans'] if p['id'] == body['id']), None)
            if (current and current.get('status') == 'active' and current.get('initialAt') == body['triggerAt']
                    and current['recurrence'] == body['recurrence'] and current['title'] == body['title']
                    and current['version'] > body['version']):
                conn.schedule_edit_pending = None
                conn.schedule_pending = None
                conn.schedule_operation_complete = True
                remember(conn, 'plan', current)
                return success(mode, current['title'], body['triggerAt'])
        elif mode == 'item':
            current = next((x for x in snapshot['items'] if x['id'] == body['id']), None)
            if current and current.get('status') == 'scheduled' and same_time(current, body) and current['version'] > body['version']:
                conn.schedule_edit_pending = None
                conn.schedule_pending = None
                conn.schedule_operation_complete = True
                remember(conn, 'item', current)
                return success(mode, current['title'], body['triggerAt'])
    save_pending(conn, kind='retry', body=body, mode=mode, source=source)
    result = await reminder_client.command(device, body)
    if mode == 'plan':
        if result.get('id') != body['id']:
            raise ValueError('wrong_plan_receipt')
        snapshot = await reminder_client.state(device)
        item = next((p for p in snapshot['plans'] if p['id'] == body['id']), None)
        if (not item or item['status'] != 'active' or item.get('version', -1) <= body['version'] or item.get('initialAt') != body['triggerAt']
                or item['recurrence'] != body['recurrence'] or item['title'] != body['title']):
            raise ValueError('plan_edit_not_verified')
        kind = 'plan'
    else:
        item = result.get('item') if mode in ('convert', 'occurrence') else result
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']
                or item.get('status') != 'scheduled' or not same_time(item, body)):
            raise ValueError('wrong_reminder_receipt')
        if mode == 'item' and item.get('id') != body['id']:
            raise ValueError('wrong_reminder_target')
        if mode == 'item' and item.get('version', -1) <= body['version']:
            raise ValueError('reminder_edit_not_verified')
        if mode == 'convert' and result.get('convertedFrom') != body['id']:
            raise ValueError('wrong_conversion_target')
        if mode == 'occurrence' and result.get('sourcePlan') != body['id']:
            raise ValueError('wrong_occurrence_target')
        kind = 'item'
    conn.schedule_edit_pending = None
    conn.schedule_pending = None
    conn.schedule_operation_complete = True
    remember(conn, kind, item)
    conn.logger.bind(tag=__name__).info('提醒修改已保存: action={}, id={}, triggerAt={}',
        body['action'], body['id'], body['triggerAt'])
    return success(mode, item['title'], body['triggerAt'])


def success(mode, title, at):
    from core.reminders.conversation import date_text
    suffix = {'convert': '原来的周期取消了，以后不再重复提醒；之前已经安排好的那一次还在。',
              'occurrence': '只改这一次，以后的提醒照旧。',
              'plan': '以后的周期按新时间来，之前已经安排好的那一次还在。'}.get(mode, '')
    return f'好，“{title}”改到{date_text(at)}提醒你。'+suffix


async def respond_edit(conn, text):
    if re.search(r'任务|待办', text) and not re.search(r'提醒|日程', text):
        return None
    if UNSAFE.search(text):
        return None
    pending = getattr(conn, 'schedule_edit_pending', None)
    if not valid_context(conn, pending):
        conn.schedule_edit_pending = None
        pending = None
    short = text.strip(' 。！!，,')
    if pending and short in ('算了', '不用了', '取消', '取消设置', '取消修改'):
        conn.schedule_edit_pending = None
        return '好，取消这次修改，原提醒保持不变。' if pending['kind'] != 'retry' else '好，不再重试了。不过上次有没有改好，还得在日程页面看一下。'
    if pending and pending['kind'] == 'retry':
        if short in RETRY:
            try:
                return await submit(conn, pending['body'], pending['mode'], pending['source'], retry=True)
            except reminder_client.OperationRejected:
                conn.schedule_edit_pending = None
                return '这条提醒已经变了，或者这次修改没被接受，我没有继续改。你先查一下现在的安排，再告诉我要改到什么时候。'
            except Exception as exc:
                conn.logger.bind(tag=__name__).warning('提醒修改重试未确认: {}', type(exc).__name__)
                return '这条有没有改好，我还没确认，也没有另外新建。你先在日程页面看一下吧。'
        if edit_request(text) or followup_answer(text) and TIME.search(text):
            return '刚才那条有没有改好，我还没确认。你说“重试”让我查一下，或者先在日程页面看一下吧。'
        return None
    if not pending and not edit_request(text):
        return None
    if pending and not followup_answer(text):
        return None
    device = conn.headers.get('device-id', '')
    if not device:
        return '我还没连上提醒设备，连好后再帮你改。'
    try:
        snapshot = await reminder_client.state(device)
        now = snapshot['serverNow']
        if pending and pending.get('target') and edit_request(text):
            ref = pending['target']
            named = [x for x in snapshot['items']+snapshot['plans'] if title_matches(x['title'], text)]
            if named and not any(x['id'] == ref['id'] for x in named):
                # A new explicit target supersedes the previous question.
                conn.schedule_edit_pending = None
                pending = None
        base = pending.get('baseNow', now) if pending else now
        source = pending['source'] if pending else text
        combined = source+'，'+text if pending else text
        recent = getattr(conn, 'schedule_recent', None)
        if not valid_context(conn, recent):
            recent = None
        whole = bool(WHOLE.search(re.sub(r'(?:不是|不再)每天', '', combined)))
        # A correction rewrites the just-created request, rather than retaining
        # its previous "每天" from hidden conversation text.
        date_source = combined
        for value in snapshot['items']+snapshot['plans']:
            date_source = date_source.replace(value['title'], '')
        date = reference_date(date_source, base)
        # The date after “改到” is a new field, not the original target date.
        selection_source = re.split(EDIT, date_source, maxsplit=1)[0]
        selection_date = reference_date(selection_source, base)
        if pending and pending.get('target'):
            ref = pending['target']
            pool = snapshot['plans'] if ref['kind'] == 'plan' else snapshot['items']
            values = [dict(x, kind=ref['kind']) for x in pool if x['id'] == ref['id'] and x['status'] not in ('cancelled', 'completed', 'expired', 'missed', 'dispatching')]
            if values and values[0]['version'] != ref['version']:
                conn.schedule_edit_pending = None
                return '刚才这条提醒已经变了，你再告诉我要改哪条、改到什么时候吧。'
            if values and ref.get('occurrenceAt'):
                values[0]['occurrenceAt'] = ref['occurrenceAt']
        else:
            values = targets(snapshot, combined, whole, selection_date, recent)
        if pending and pending.get('choices'):
            index = re.search(r'第([一二三四五六七八九十0-9]+)', text)
            if index and 0 <= number(index[1])-1 < len(pending['choices']):
                ref = pending['choices'][number(index[1])-1]
                values = [x for x in values if x['id'] == ref['id'] and x['kind'] == ref['kind']]
                if values and values[0]['version'] != ref['version']:
                    conn.schedule_edit_pending = None
                    return '这条提醒已经变了，你先查一下现在的安排，再告诉我要怎么改吧。'
        if len(values) != 1:
            if not values:
                return '没有找到这条提醒。你说一下原来哪天提醒什么事，我再找找。'
            choices = [dict(kind=x['kind'], id=x['id'], version=x['version']) for x in values[:5]]
            save_pending(conn, kind='choice', source=source, choices=choices, baseNow=base)
            from core.reminders.conversation import date_text
            return '要修改哪一条？'+'；'.join(f'第{i+1}条，{x["title"]}（{"周期" if x["kind"] == "plan" else "本次提醒"}，{date_text(x.get("scheduledAt") or x.get("dueAt", x.get("nextAt")))}）' for i, x in enumerate(values[:5]))+'。'
        selected = values[0]
        time_source = combined.replace(selected['title'], '')
        changes = list(re.finditer(r'改到|改为|改成|修改(?:为|到)?|调整(?:为|到)?', time_source))
        if changes and not SHIFT.search(time_source):
            time_source = time_source[changes[-1].end():]
        if pending and pending['kind'] == 'fields' and (CLOCK.search(text) or SHIFT.search(text)):
            # A concrete answer replaces the ambiguous clock/duration, while
            # retaining the date from the question when the answer omits it.
            time_source = text
            if not reference_date(text, base) and date and not re.search(rf'{NUM}[日号]|\d{{4}}年', text):
                time_source = datetime.fromisoformat(date).strftime('%Y年%m月%d日，')+text
        mode = 'item'
        ref = dict(kind=selected['kind'], id=selected['id'], version=selected['version'])
        if selected['kind'] == 'plan':
            if ONCE.search(combined) or CORRECTION.search(source) and date and not WHOLE.search(source):
                mode = 'convert'
            elif whole:
                mode = 'plan'
            elif selection_date or (pending and pending.get('target', {}).get('occurrenceAt')) or re.search(r'这次|这一次', combined):
                mode = 'occurrence'
                ref['occurrenceAt'] = selected.get('occurrenceAt') or (occurrence(selected, selection_date) if selection_date else selected['nextAt'])
                if ref['occurrenceAt'] is None:
                    return '原来的重复提醒不在这一天，你想改哪一次，还是以后都换个时间？'
            else:
                save_pending(conn, kind='scope', source=source, target=ref, baseNow=base)
                return '这条会重复提醒，你只改这一次，还是以后都改？也可以改成只提醒一次。'
        shift = SHIFT.search(time_source)
        if shift:
            amount = 0.5 if shift[2] == '半' else number(shift[2])
            delta = int(amount*(3600000 if '小时' in shift[3] else 60000 if shift[3] == '分钟' else 1000))
            old_at = (selected.get('scheduledAt') or selected['dueAt']) if mode == 'item' else ref.get('occurrenceAt', selected['nextAt'])
            at = old_at+(-delta if shift[1] in ('提前', '往前推') else delta)
            rule = selected.get('recurrence', 'once')
            question = None if delta > 0 and now < at <= now+5*366*86400000 else '调整后的时间必须在未来，请重新说明日期和时间。'
        else:
            time_source,date_question = expand_day(time_source, base)
            if date_question:
                save_pending(conn, kind='fields', source=combined, target=ref, baseNow=base)
                return date_question+' 原来的安排还没改。'
            if not reference_date(time_source, base):
                old_at = (selected.get('scheduledAt') or selected['dueAt']) if mode == 'item' else ref.get('occurrenceAt', selected['nextAt'])
                day = datetime.fromtimestamp(old_at/1000, ZONE)
                time_source = f'{day.year}年{day.month}月{day.day}日，'+time_source
            at, rule, question = parse_time(time_source, base)
        if question:
            save_pending(conn, kind='fields', source=combined, target=ref, baseNow=base)
            return question+' 原来的安排还没改。'
        if at <= now:
            save_pending(conn, kind='fields', source=combined, target=ref, baseNow=base)
            return '这个时间已经过去了，请重新说明日期和时间。原提醒还没有修改。'
        if mode == 'item' and rule != 'once':
            return '这条只提醒一次。想改成重复提醒，你先在日程页面另外设置，这条我先保留原样。'
        body = dict(action='edit', id=selected['id'], version=selected['version'], title=selected['title'], triggerAt=at)
        if mode == 'convert':
            body['action'] = 'plan_to_once'
        elif mode == 'occurrence':
            body.update(action='plan_occurrence_edit', occurrenceAt=ref['occurrenceAt'])
        elif mode == 'plan':
            body.update(action='plan_edit', recurrence=rule if rule != 'once' else selected['recurrence'])
        return await submit(conn, body, mode, source)
    except reminder_client.OperationRejected:
        conn.schedule_edit_pending = None
        return '这条提醒已经变了，或者这次修改没被接受，我没有继续改。你先查一下现在的安排，再告诉我要改到什么时候。'
    except Exception as exc:
        conn.logger.bind(tag=__name__).warning('提醒修改未确认: {}', type(exc).__name__)
        if (getattr(conn, 'schedule_edit_pending', None) or {}).get('kind') == 'retry':
            return '这次有没有改好，我还没确认。你先在日程页面看一下，或者说“重试”让我查一下吧。'
        return '这次没查到对应的提醒，还没有改。过会儿你再告诉我哪件事、原来哪天提醒、想改到什么时候吧。'
