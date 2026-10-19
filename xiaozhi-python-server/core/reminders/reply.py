"""Resolve spoken reminder groups against recent context and verify every receipt."""
import asyncio
import re
import time
from core.reminders import client as reminder_client
from core.reminders.parser import number

CONFIRMABLE={'dispatching','awaiting_confirmation','retry_pending','delivery_unknown','missed','expired'}
ALL_ACK=re.compile(
    r'(?:好的?|嗯)?(?:我)?(?:刚才的?|刚刚的?|这|那)?'
    r'(?:(?P<count>[一二两三四五六七八九十百0-9]+)(?:条|个|件)(?:提醒|事情|事)?|'
    r'(?:这些|那些|所有|全部)(?:未确认的|没确认的)?(?:提醒)?)?'
    r'(?:我)?(?:都|全都|全部|全|一并|一起)(?:已经|已)?'
    r'(?:收到(?:提醒)?|知道|确认收到|确认|完成)(?:了|啦|了啊|了呀)?')


def all_ack(text):
    # Only a whole affirmative sentence gets the fast path. Mixed requests,
    # quoted speech, questions and negatives stay with semantic routing.
    return ALL_ACK.fullmatch(re.sub(r'\s+','',text).strip(' 。！!，,'))


def remember(conn, items, total=None):
    device=getattr(conn,'headers',{}).get('device-id','')
    if not device or not items: return
    conn.reminder_reply_context=dict(session=getattr(conn,'session_id',None),device=device,
        expires=time.monotonic()+180,items=[dict(id=x['id'],title=x['title']) for x in items],
        total=len(items) if total is None else total)


def recent(conn):
    context=getattr(conn,'reminder_reply_context',None)
    if context and (context['expires']<=time.monotonic()
                   or context['session']!=getattr(conn,'session_id',None)
                   or context['device']!=getattr(conn,'headers',{}).get('device-id','')):
        conn.reminder_reply_context=None
        context=None
    return context


def reply_candidate(conn,text):
    return bool(recent(conn) and re.search(r'收到|知道|都|全部|刚才|那条|这条|看到了|听到了|记住了|明白了',text))


def resolve_all(conn, text, eligible, unanswered_count):
    match=all_ack(text)
    if not match: return None
    count=number(match['count']) if match['count'] else None
    context=recent(conn)
    explicit_global=bool(re.search(r'所有(?:未确认的|没确认的)?提醒|全部(?:未确认的|没确认的)提醒',text))
    if context and not explicit_global:
        if context['total']>len(context['items']) or count is not None and count!=context['total']:
            return dict(action='ambiguous',targetId=None)
        ids={x['id'] for x in context['items']}
        targets=[x['id'] for x in eligible if x['id'] in ids]
    else:
        if unanswered_count>len(eligible) or count is not None and count!=len(eligible):
            return dict(action='ambiguous',targetId=None)
        targets=[x['id'] for x in eligible]
    return dict(action='confirm',targetIds=targets)


async def confirm_group(conn, device, selected, *, action='confirm', trigger_at=None):
    generation=getattr(conn,'input_generation',0)
    session=getattr(conn,'session_id',None)
    outcomes={}
    semaphore=asyncio.Semaphore(4)
    def current():
        return (getattr(conn,'input_generation',0)==generation and getattr(conn,'session_id',None)==session
                and conn.headers.get('device-id','')==device)
    async def confirm(item):
        async with semaphore:
            if not current(): return
            try:
                result=await asyncio.wait_for(reminder_client.command(device,
                    dict(action=action,id=item['id'],version=item['version'],**({'triggerAt':trigger_at} if trigger_at is not None else {'seconds':300} if action=='snooze' else {}))),5)
                if (not isinstance(result,dict) or result.get('id')!=item['id']
                        or result.get('status')!=('completed' if action=='confirm' else 'scheduled') or result.get('version',-1)<=item['version']):
                    raise ValueError('提醒操作回执不匹配')
                if action=='snooze' and (not result.get('dueAt') or trigger_at is not None and result['dueAt']!=trigger_at):
                    raise ValueError('延后时间回执不匹配')
                outcomes[item['id']]=True
            except Exception as exc:
                outcomes[item['id']]=False
                conn.logger.bind(tag=__name__).warning('提醒确认未核实: id={}, error={}',item['id'],type(exc).__name__)
    try:
        await asyncio.wait_for(asyncio.gather(*(confirm(x) for x in selected)),12)
    except TimeoutError:
        pass # Report only verified receipts; pending requests may have reached Java.
    confirmed=[x for x in selected if outcomes.get(x['id'])]
    unknown=[x for x in selected if not outcomes.get(x['id'])]
    if confirmed:
        from core.device.face import send_face
        if current(): await send_face(conn,'reminder_ack')
    if not unknown:
        if current():
            conn.schedule_operation_complete=True
            conn.reminder_response_until=0
            conn.reminder_reply_context=None
            conn.schedule_pending=None
        amount={1:'这条',2:'这两条',3:'这三条',4:'这四条'}.get(len(confirmed),f'这{len(confirmed)}条')
        if action=='snooze':
            return f'好，{amount}提醒都重新安排好了。' if trigger_at is not None else f'好，{amount}提醒都延后五分钟。'
        return f'好，{amount}提醒都记为收到了。' if len(confirmed)>1 else '好，这条提醒记为收到了。'
    def titles(rows):
        return '、'.join('“'+x['title'][:36]+'”' for x in rows[:4])+('等提醒' if len(rows)>4 else '')
    prefix=titles(confirmed)+('已经记为收到了；' if action=='confirm' else '已经重新安排好了；') if confirmed else ''
    detail='有没有记为收到，我还没确认，你先在日程页面看一下吧。' if action=='confirm' else '延后有没有保存成功，我还没确认，你先在日程页面看一下吧。'
    return prefix+titles(unknown)+detail


def resolve_all_snooze(conn, text, eligible, total):
    # Restrict the fast path to an explicit group and the agreed five-minute snooze.
    compact=re.sub(r'\s+','',text).strip(' 。！!，,')
    match=re.fullmatch(r'(?:把)?(?P<scope>(?:这|那)?[一二两三四五六七八九十0-9]+(?:条|个|件)(?:提醒)?|全部(?:提醒)?|所有提醒|这些(?:提醒)?|都)(?:都|一起|一并)?(?:延后|推迟|稍后|晚点)(?:五分钟|5分钟)?(?:后)?(?:再提醒(?:我)?)?(?:吧|了)?',compact)
    if not match: return None
    scope=match['scope']
    group=resolve_all(conn,('全部' if scope=='都' else scope)+'都收到了',eligible,total)
    if group and group['action']!='ambiguous': group['action']='snooze'
    return group
