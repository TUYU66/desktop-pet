"""One owner for field answers; explicit operations never inherit a draft action."""
import re
import time

ATTRS = ('schedule_pending', 'schedule_batch_pending', 'schedule_edit_pending', 'schedule_control_pending')
CANCEL = {'算了', '不用了', '取消', '取消设置', '取消修改'}
RETRY = {'重试', '再试一次', '重试一次', '核实刚才的设置', '核实刚才的修改'}


def read_query(text):
    """Recognize only read operations, including normal interrogative punctuation."""
    from core.reminders.batch import outside_quotes
    masked = outside_quotes(text)
    if re.search(r'如果|假如|比如|举例|引用|他说|她说|不要|别|收到|知道了|提醒我|叫我|暂停|恢复|取消|关闭|关掉|修改|改成|改到|延后|提前', masked):
        return False
    return bool(re.search(r'提醒|日程|周期计划|(?:今天|明天).{0,8}安排', masked) and (
        re.fullmatch(r'(?:今天|明天)(?:的)?(?:提醒|日程)', masked.strip(' 。！？!?，,'))
        or re.search(r'查看|查询|列出|有哪些|有什么|什么提醒|什么日程|哪些.*(?:提醒|日程|计划)|日程安排|日程列表|(?:今天|明天).{0,8}安排', masked)))


def fields(text):
    from core.reminders.batch import temporal_answer
    from core.reminders.edits import followup_answer
    return temporal_answer(text) or bool(re.fullmatch(r'第[一二三四五六七八九十0-9]+条?',text)) or followup_answer(text)


def write_blocked(text):
    """ASR often omits question marks; status questions are not write consent."""
    if re.search(r'[？?]|(?:吗|么|呢|了没有|了没)[\s。！!，,]*$|是否|是不是|有没有|能否|能不能|可不可以|会不会|'
            r'^(?:请问)?(?:怎么|如何|为什么)|(?:怎么|如何|为什么).{0,20}(?:取消|暂停|恢复|修改|调整|延后|提前)', text):
        return True
    return bool(re.search(r'(?:不要|别|先不|暂不|不用|无需|不想|没有|没|不是).{0,12}'
        r'(?:取消|暂停|恢复|修改|调整|改到|改成|改为|延后|推迟|提前|关闭|关掉|确认|完成)', text))


async def route(conn, text, handler):
    from core.reminders.reply import all_ack, reply_candidate
    from core.reminders.edits import edit_request
    from core.reminders.batch import MARKER, outside_quotes
    masked=outside_quotes(text)
    query = read_query(text)
    if re.search(r'如果|假如|比如|引用|他说|她说|不要创建|别创建|不要设置|别设置',masked): return None
    if write_blocked(masked) and not query: return None
    if MARKER.search(text) and not MARKER.search(masked): return None
    short = text.strip(' 。！!，,')
    active = {}
    expired = False
    for attr in ATTRS:
        value = getattr(conn,attr,None)
        if not value: continue
        if (value.get('expires',0)<=time.monotonic() or value.get('session')!=getattr(conn,'session_id',None)
                or value.get('device')!=conn.headers.get('device-id','')):
            setattr(conn,attr,None); expired=True; continue
        active[attr] = value
    explicit_create = bool(MARKER.search(outside_quotes(text))) and not edit_request(text)
    explicit_edit = edit_request(text)
    control = query or bool(all_ack(text)) or reply_candidate(conn,text) or bool(re.search(r'收到|知道了|查看|查询|列出|有哪些|什么日程|什么提醒|日程安排|日程列表|今天.{0,8}安排|明天.{0,8}安排|周期计划.*有什么|提醒.*有什么|暂停|恢复|取消.+|不用提醒|关闭提醒|关掉吧',text)
                   or re.fullmatch(r'(?:今天|明天)(?:的)?(?:提醒|日程)',short))
    if explicit_create or explicit_edit or control:
        conn.schedule_invitation=None  # A new explicit operation is not consent to an older offer.
    if re.search(r'收到|知道了',text) and re.search(r'吗[ 。！!，,]*$|是否|是不是|没收到|没有收到|不要.*(?:确认|收到|完成)|[“”"]',text): return None
    if expired and not active and (short in RETRY or fields(short) and not control and not explicit_create and not explicit_edit):
        return '刚才的日程追问已失效，你先在日程页面看一下存了哪些，再告诉我要安排什么吧。'
    owner = None
    if short in CANCEL or short in RETRY:
        if active:
            owner = max(active,key=lambda attr: active[attr].get('order',0))
            if sum(value.get('order',0)==active[owner].get('order',0) for value in active.values())>1:
                return '有几件事都还没说完，你告诉我要继续安排哪件吧。'
    elif control:
        if ('schedule_control_pending' in active
                and active['schedule_control_pending'].get('action')=='confirm'
                and (all_ack(text) or reply_candidate(conn,text) or re.search(r'收到|知道了',text))):
            owner='schedule_control_pending'
    elif explicit_edit:
        from core.reminders.creation_recovery import rejected_field
        latest = max(active,key=lambda attr: active[attr].get('order',0)) if active else None
        # "改到明天下午三点" may correct a refused creation, without editing
        # any unrelated saved reminder. Only the current field owner can do so.
        if latest and rejected_field(active[latest], short):
            owner = latest
        else:
            owner = 'schedule_edit_pending' if 'schedule_edit_pending' in active else None
    elif explicit_create:
        kind = 'schedule_batch_pending' if len(list(MARKER.finditer(outside_quotes(text))))>1 else 'schedule_pending'
        if kind in active:
            return '刚才的提醒还没设置完，或者还没确认存好。先把刚才那条说完、重试或取消，再设新的吧。'
    elif fields(short) and active:
        owner = max(active,key=lambda attr: active[attr].get('order',0))
        if sum(value.get('order',0)==active[owner].get('order',0) for value in active.values())>1:
            return '有几件事都还没说完，你告诉我要继续安排哪件吧。'
    else:
        # Casual speech must not become a field answer or an implicit acknowledgement.
        if active: return None
    # Temporarily expose only the selected draft; restore unrelated drafts afterward.
    for attr in ATTRS: setattr(conn,attr,None)
    if owner:
        target = 'schedule_pending' if owner=='schedule_control_pending' else owner
        setattr(conn,target,active[owner])
    else: target = None
    try:
        answer = await handler(conn,text)
    finally:
        produced = {attr:getattr(conn,attr,None) for attr in ATTRS}
        if owner=='schedule_control_pending':
            produced[owner] = produced['schedule_pending']; produced['schedule_pending'] = None
        elif control and produced['schedule_pending']:
            produced['schedule_control_pending'] = produced['schedule_pending']; produced['schedule_pending'] = None
        for attr,old in active.items():
            if attr!=owner and produced.get(attr) is None: produced[attr]=old
        for attr,value in produced.items():
            if value:
                value.setdefault('session',getattr(conn,'session_id',None))
                value.setdefault('device',conn.headers.get('device-id',''))
                if 'order' not in value:
                    conn.schedule_draft_order=getattr(conn,'schedule_draft_order',0)+1
                    value['order']=conn.schedule_draft_order
            setattr(conn,attr,value)
    if answer and (owner and not produced.get(owner) or getattr(conn,'schedule_operation_complete',False)):
        waiting={attr:value for attr,value in produced.items() if value}
        if waiting:
            next_owner=max(waiting,key=lambda attr:waiting[attr].get('order',0))
            value=waiting[next_owner]
            label=value.get('title') or value.get('source') or value.get('text','日程')
            if next_owner=='schedule_batch_pending':
                index=value.get('field',0)
                label=value['batch'][index].get('title','这组提醒')
            answer+=f' 另有“{label[:120]}”的草稿待处理；下一条单独的时间回答将用于这份草稿。'
    return answer
