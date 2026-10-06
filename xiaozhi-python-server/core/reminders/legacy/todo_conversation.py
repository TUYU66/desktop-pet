# Retired todo feature: retained for historical reference; no active entry imports this module.
"""确定性任务授权与目标选择；模型不决定完成/删除，查询不写状态。"""
import asyncio
import re
import time
import uuid
from datetime import datetime, timedelta
from core.reminders.legacy import todo_client as task_client
from core.reminders.parser import parse_time, title_from, ZONE, NUM, number, expand_day

CREATE=re.compile(r'(?:添加|新增|创建|记下|记|加|安排)(?:一个|个|一条|条)?(?:任务|待办)[：:，,\s]*(.+)')
QUERY=re.compile(r'有哪些|有什么|查看|查询|列出|看看|还有|什么安排|哪些安排|剩下')
FINISH=re.compile(r'做完了|完成了|拿到了|(?:收拾|整理|打扫|清理|取|拿|提交|交)(?:好|完)[^，,。！？?]{0,20}了|标记(?:为)?完成|标为完成|标记(?:为)?已完成|(?:任务|待办).{0,12}完成|完成.{0,30}(?:任务|待办)')
NEGATIVE=re.compile(r'如果|假如|比如|举例|引用|是不是|是否|没有完成|没完成|(?:还没|没|没有|尚未).{0,12}(?:好|完|拿到)|(?:准备|打算|计划|想要).{0,20}(?:收拾|整理|打扫|清理|完成)|(?:不要|别).{0,20}(?:添加|创建|完成|删除|标记)|他说|她说|[“”"？?]|(?:好|完|完成)了吗')


def relevant(text):
    return isinstance(text,str) and bool(FINISH.search(text) or re.search(r'任务|待办|逾期|无日期|继续说|接着说|(?:取消|关闭).{0,24}提醒',text))


def task_date(text, now):
    current=datetime.fromtimestamp(now/1000,ZONE)
    dates=list(re.finditer(rf'(?:(\d{{4}})年)?({NUM})月({NUM})[日号]',text))
    explicit=dates[-1] if dates else None
    if explicit: return datetime(int(explicit[1] or current.year),number(explicit[2]),number(explicit[3]),tzinfo=ZONE).date().isoformat()
    expanded,question=expand_day(text,now)
    if not question and expanded!=text: return task_date(expanded,now)
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


def todo_dates(text, now):
    # Dates are inclusive calendar days; the stored deadline is next-day midnight.
    match=re.search(r'截止(?:日期|时间)?(?:为|到|是)?[：: ]*([^，,；;]+)',text)
    suffix=re.search(r'([^，,；;]+?)(?:为)?截止',text) if not match else None
    end_source=match[1] if match else suffix[1] if suffix else ''
    end=task_date(end_source,now) if end_source else None
    remainder=text[:match.start()]+text[match.end():] if match else text[:suffix.start()]+text[suffix.end():] if suffix else text
    start=task_date(remainder,now)
    interval=re.search(r'(.+?)(?:到|至)(.+)',remainder)
    if interval and not end:
        start=task_date(interval[1],now); end=task_date(interval[2],now)
    deadline=int((datetime.fromisoformat(end).replace(tzinfo=ZONE)+timedelta(days=1)).timestamp()*1000) if end else None
    return start,deadline


def task_date_source(text):
    # 提醒和截止的日期各自存放，不推断成明确的任务日期。
    return '，'.join(re.split(r'截止|提醒',part,1)[0] for part in re.split(r'[，,；;]',text))


def scope_for(text):
    if '已完成' in text or re.search(r'(?<!未)(?<!没)(?<!没有)完成的(?:任务|待办)',text): return 'completed','已完成的任务'
    if '已取消' in text: return 'cancelled','已取消的任务'
    if '逾期' in text or '到期未完成' in text: return 'expired','到期未完成的待办'
    if '无日期' in text or '没有日期' in text: return 'undated','无日期待办'
    if '明天' in text: return 'tomorrow','明天的任务'
    if '今天' in text: return 'today','今天的任务'
    if '接下来' in text or '下一项' in text: return 'upcoming','接下来的任务'
    if '未完成' in text or '待办' in text or QUERY.search(text): return 'all','未完成任务'
    return 'today','今天的任务'


def describe(item):
    from core.reminders.conversation import date_text, recurrence_text
    if 'recurrence' in item and 'nextAt' in item:
        return item['title']+'，'+recurrence_text(item['recurrence'],item['initialAt'])+'，下次安排：'+date_text(item['nextAt'])
    if item.get('legacy'):
        label='独立提醒：'+item['title']+'，'+date_text(item.get('originalDueAt') or item['dueAt'])
        reminder=item
    else:
        label=item['title']
        if item.get('taskDate'): label+='，安排在'+item['taskDate']+'（北京时间）'
        if item.get('deadlineAt') is not None: label+='，截止日期'+datetime.fromtimestamp((item['deadlineAt']-1)/1000,ZONE).date().isoformat()
        if item.get('reminderEnabled') and item.get('remindAt') is not None: label+='，提醒时间'+date_text(item['remindAt'])
        else: pass
        if not any(item.get(k) is not None for k in ('taskDate','deadlineAt','remindAt')): label+='，无日期'
        reminder=item.get('reminder') or {}
    if reminder.get('status') in ('awaiting_confirmation','retry_pending','delivery_unknown','missed'):
        label+='，提醒尚未回应'
        if reminder['status']=='retry_pending': label+='，将在'+date_text(reminder['nextAttemptAt'])+'再次提醒'
    elif reminder.get('status')=='completed': label+='，提醒已回应'
    return label


async def list_reply(conn,device,text,continuing=False):
    context=getattr(conn,'task_query',None) if continuing else None
    if context and (context['expires']<time.monotonic() or context['session']!=conn.session_id):
        conn.task_query=None; return None
    if continuing and not context: return None
    scope,label=(context['scope'],context['label']) if context else scope_for(text)
    offset=context['offset'] if context else 0
    snapshot=await task_client.state(device,scope,offset,5)
    if not context:
        legacy=[]  # 日程提醒通过独立入口查询。
        context=dict(scope=scope,label=label,offset=0,legacy=legacy,legacyOffset=0,session=conn.session_id,expires=time.monotonic()+180)
    def sort_key(item):
        at=int(datetime.fromisoformat(item['taskDate']).replace(tzinfo=ZONE).timestamp()*1000) if item.get('taskDate') else (
            item.get('deadlineAt') or item.get('remindAt') or item.get('originalDueAt') or item.get('dueAt') or float('inf'))
        return at,item['title'],item['id']
    start=context['legacyOffset']
    items=sorted(list(snapshot['items'])+context['legacy'][start:start+5],key=sort_key)[:5]
    task_count=sum(not item.get('legacy',False) for item in items)
    context['offset']=offset+task_count
    context['legacyOffset']+=len(items)-task_count
    more=snapshot['hasMore'] or task_count<len(snapshot['items']) or context['legacyOffset']<len(context['legacy'])
    context['expires']=time.monotonic()+180
    conn.task_query=context if more else None
    conn.schedule_operation_complete=True
    count=snapshot['total']+len(context['legacy'])
    if not items: return '暂时没有符合条件的'+label+'。'
    intro=('继续为你列出：' if continuing else label+'共'+str(count)+'项：')
    return intro+'；'.join(describe(item) for item in items)+'。'+('还可以说“继续说”查看后面的事项。' if more else '')


def context_valid(conn,pending):
    return pending and pending.get('session')==conn.session_id and pending.get('expires',0)>time.monotonic()


async def send_operation(conn,device,body,label):
    conn.task_pending=dict(kind='retry',body=body,label=label,session=conn.session_id,expires=time.monotonic()+1800)
    result=await task_client.command(device,body)
    receipt=result.get('plan') if body['action'].startswith('plan_') else (result.get('item') or result.get('plan'))
    expected=body['requestId'] if body['action']=='create' else body['id']
    if not isinstance(receipt,dict) or receipt.get('id')!=expected: raise ValueError('任务回执目标不符')
    if body['action']=='complete' and (receipt.get('status')!='completed' or not receipt.get('title')):
        raise ValueError('任务完成状态未确认')
    conn.task_pending=None; conn.task_query=None; conn.schedule_operation_complete=True
    if body['action']=='create':
        item=result.get('item'); plan=result.get('plan')
        if plan: return '周期任务已保存，每次任务独立完成；暂停或取消整个周期需要单独说明。'
        return '任务已添加：'+describe(item)+'。'
    if body['action']=='complete': return '待办已标记完成：'+receipt['title']+'。'
    if body['action']=='restore': return '已恢复为待办。'
    if body['action']=='disable_reminder': return '提醒已关闭，任务仍保留在待办中。'
    if body['action']=='delete': return '任务已删除，关联提醒已停止。'
    if body['action']=='cancel': return '待办已取消。'
    if body['action'].startswith('plan_'): return '周期已更新，已经生成的本次任务独立保留，可单独操作。'
    return '任务已更新：'+describe(result['item'])+'。'


async def respond(conn,text):
    if re.search(r'日程|周期|提醒|叫我',text) and not re.search(r'任务|待办',text): return None
    short=text.strip(' 。！!，,？?')
    pending=getattr(conn,'task_pending',None)
    if not context_valid(conn,pending): conn.task_pending=None; pending=None
    continuation=short in ('继续说','接着说','继续','还有呢')
    if not relevant(text) and not pending and not (continuation and getattr(conn,'task_query',None)): return None
    device=conn.headers.get('device-id','')
    if not device: return '暂时无法确定任务账户，请连接设备后重试。'
    try:
        if continuation: return await list_reply(conn,device,text,True)
        if short in ('任务安排','待办') or re.fullmatch(r'(?:今天|明天|接下来|未完成|逾期|已完成|已取消)(?:的)?(?:任务|待办)',short) or QUERY.search(text) and re.search(r'任务|待办',text):
            return await list_reply(conn,device,text)
        if NEGATIVE.search(text): return None
        if pending and short in ('算了','不用了','取消设置'):
            conn.task_pending=None; return '好，这次操作先取消，已有任务保持原样。'
        if pending and pending['kind']=='fields' and not relevant(text) and not re.search(r'点|时|分|秒|天|月|日|号|周|上午|下午|晚上|截止|提醒',text): return None
        if pending and pending['kind']=='choice' and not re.search(r'第[一二三四五六七八九十0-9]+',text):
            if not any(item.get('title','') and item['title'] in text for item in pending.get('candidates',[])): return None
        if pending and pending['kind']=='retry' and short in ('重试','再试一次','重试一次'):
            return await send_operation(conn,device,pending['body'],pending['label'])
        if pending and pending['kind']=='retry':
            if not relevant(text): return None
            return '上一次任务操作的结果还没有核实。请在网页核实，或说“重试”核实同一个操作。'
        snapshot=await task_client.state(device)
        now=snapshot['serverNow']
        original=pending.get('source',text) if pending else text
        combined=original+'，'+text if pending else text
        creating=CREATE.search(original)
        if creating:
            title=(pending or {}).get('title') or creating[1].strip(' ，。！!')
            title=re.split(r'[，,；;](?=截止|提醒|不提醒|无需提醒|任务日期|开始|今天|明天|后天|大后天|下周|本周|\d)',title)[0]
            title=title_from('提醒我'+title)
            if not title: return '请告诉我要添加的任务内容。'
            timing=combined.replace(title,'',1)
            if re.search(r'(?<!不)(?<!无需)提醒|每天|每周|工作日',timing):
                conn.task_pending=None
                return '待办只记录开始和截止日期。如果需要提醒，请说“明天下午三点提醒我拿快递”；重复提醒可说“每周一、三、五下午三点提醒我拿快递”。'
            date,deadline=todo_dates(timing,now)
            if not date or deadline is None:
                conn.task_pending=dict(kind='fields',source=combined,title=title,session=conn.session_id,expires=time.monotonic()+180)
                return '请补充待办的开始日期和截止日期，范围是今天至未来7天，例如“明天开始，后天截止”。'
            body=dict(action='create',requestId=str(uuid.uuid4()),title=title,taskDate=date,deadlineAt=deadline,reminderEnabled=False,remindAt=None,recurrence='once')
            return await send_operation(conn,device,body,'添加任务')
        action=(pending or {}).get('action')
        plan_scope=bool(re.search(r'整个|所有后续|周期|每天|每周',original))
        if action is None:
            if FINISH.search(text): action='complete'
            elif re.search(r'删除|删掉',text): action='delete'
            elif re.search(r'关闭|取消|不用',text) and '提醒' in text: action='disable_reminder'
            elif re.search(r'取消',text) and re.search(r'任务|待办|周期',text): action='cancel'
            elif re.search(r'恢复|重新待办',text): action='restore'
            elif re.search(r'暂停',text) and plan_scope: action='plan_pause'
            elif re.search(r'修改|改到|改为|改成|设置',text) and re.search(r'任务|待办',text): action='edit'
            else: return None
        if plan_scope:
            action={'cancel':'plan_cancel','restore':'plan_resume'}.get(action,action)
            if action=='disable_reminder' and re.search(r'整个|所有后续|周期',original): action='plan_cancel'
            if not action.startswith('plan_'): return '本次任务和整个周期是两种操作。完成本次请带上任务内容，暂停或取消整个周期请明确说明。'
            candidates=[p for p in snapshot['plans'] if p.get('taskEnabled') and p['status']!='cancelled']
        else:
            scopes=('completed','cancelled') if action=='restore' else ('all','completed','cancelled') if action=='delete' else ('all',)
            matched=[]
            results=await asyncio.gather(*(task_client.state(device,scope,match=combined) for scope in scopes))
            for result in results:
                if result['hasMore']: return '同名任务较多，请在网页按日期筛选后操作。'
                matched+=result['items']
            if action=='disable_reminder' and not matched and not re.search(r'任务|待办',text): return None
            if action=='restore' and not matched:
                results=await asyncio.gather(*(task_client.state(device,scope) for scope in scopes))
                matched=[item for result in results for item in result['items']]
            candidates=(matched or snapshot['items']) if action!='restore' else matched
            # 当前提醒对应的任务可能在列表第200项之后，不能只查首屏。
            observed=[r for r in snapshot['reminders'] if r.get('taskId') and r['status'] in ('awaiting_confirmation','retry_pending','delivery_unknown','missed')]
            pronoun=action=='complete' and short in ('做完了','完成了','拿到了','这个做完了')
            bound=(pending or {}).get('candidates',[]) if (pending or {}).get('kind')=='choice' else []
            task_ids=list(dict.fromkeys(r['taskId'] for r in observed)) if pronoun else list(dict.fromkeys(x['id'] for x in bound if not x.get('virtual')))
            if task_ids:
                if len(task_ids)>5: return '待回应事项较多，请带上已完成的任务完整名称，或在网页操作。'
                fresh=await asyncio.gather(*(task_client.item(device,task_id) for task_id in task_ids))
                candidates=[x for x in fresh if x['status']=='open' or action in ('restore','delete')]+[x for x in candidates if x.get('virtual')]
        timing=combined
        for candidate in candidates: timing=timing.replace(candidate['title'],'',1)
        # 编辑句中“改到明天”是新字段，不能用它排除原来安排在今天的任务。
        selection_timing=re.split(r'修改|改到|改为|改成|设置',timing,1)[0] if action=='edit' else timing
        requested_day=task_date(selection_timing,now)
        if requested_day and not plan_scope:
            candidates=[x for x in candidates if (x.get('taskDate') or (datetime.fromtimestamp((x.get('deadlineAt') or x.get('remindAt') or x.get('occurrenceAt'))/1000,ZONE).date().isoformat()
                if any(x.get(k) is not None for k in ('deadlineAt','remindAt','occurrenceAt')) else None))==requested_day]
        named=[x for x in candidates if x['title'] in combined]
        if not named:
            named=[x for x in candidates if len(re.sub(r'^(?:拿|取|做|整理|完成|提交|交|喝|吃)','',x['title']))>=2
                and re.sub(r'^(?:拿|取|做|整理|完成|提交|交|喝|吃)','',x['title']) in combined]
        index=re.search(r'第([一二三四五六七八九十0-9]+)',text) if pending and pending.get('choices') else None
        if index and 0<=number(index[1])-1<len(pending['choices']):
            candidates=[x for x in candidates if x['id']==pending['choices'][number(index[1])-1]]
        elif named: candidates=named
        elif action=='complete' and short in ('做完了','完成了','拿到了','这个做完了'):
            eligible={r.get('taskId') for r in snapshot['reminders'] if r['status'] in ('awaiting_confirmation','retry_pending','delivery_unknown','missed')}
            candidates=[x for x in candidates if x['id'] in eligible]
        else:
            if action=='complete' and not re.search(r'任务|待办',text) and short not in ('做完了','完成了','拿到了','这个做完了'): return None
            conn.task_pending=dict(kind='choice',action=action,source=original,candidates=candidates[:5],choices=[x['id'] for x in candidates[:5]],session=conn.session_id,expires=time.monotonic()+180)
            return '请带上要操作的任务完整名称。'+('候选为：'+'；'.join(f"第{i+1}条，{describe(x)}" for i,x in enumerate(candidates[:5]))+'。' if candidates else '暂时没有找到对应待办。')
        if len(candidates)!=1:
            if not candidates:
                if action=='complete': return None
                return '没有找到对应任务。请带上完整事项名称，或在网页筛选后操作。'
            conn.task_pending=dict(kind='choice',action=action,source=original,candidates=candidates[:5],choices=[x['id'] for x in candidates[:5]],session=conn.session_id,expires=time.monotonic()+180)
            return '请确认是哪一项：'+'；'.join(f"第{i+1}条，{describe(x)}" for i,x in enumerate(candidates[:5]))+'。'
        selected=candidates[0]
        body=dict(action=action,id=selected['id'],version=selected['version'],requestId=str(uuid.uuid4()))
        if selected.get('virtual'): body.update(planId=selected['planId'],occurrenceAt=selected['occurrenceAt'])
        if action=='edit':
            title=selected['title']; new=re.search(r'(?:内容|标题)(?:改为|改成|设为)[：:，,\s]*(.+)',combined)
            if new: title=new[1].strip(' ，。！!')[:120]
            timing=combined.replace(selected['title'],'',1)
            date=selected.get('taskDate')
            date_source=task_date_source(timing)
            if re.search(r'日期|安排在|安排到|改到|改为|改成',date_source):
                date=task_date(date_source,now) or date
            deadline=selected.get('deadlineAt')
            if re.search(r'提醒|每天|每周',timing): return '待办不设置提醒或周期，请单独创建日程提醒。'
            if '截止' in timing:
                parsed_date,new_deadline=todo_dates(timing,now)
                if new_deadline is None: return '请说明截止日期，例如“后天截止”，并带上待办名称。'
                deadline=new_deadline
                date=parsed_date or date
            body.update(title=title,taskDate=date,deadlineAt=deadline,reminderEnabled=False,remindAt=None,taskDeviceId=None)

        return await send_operation(conn,device,body,action)
    except task_client.OperationRejected as exc:
        conn.task_pending=None
        if exc.code==409: return '任务记录已变化，本次操作没有执行。请重新查询，再明确要操作的事项。'
        if exc.code==404: return '对应任务已不存在或已删除。请重新查询后选择事项。'
        return '任务服务已拒绝本次请求：'+str(exc)+'。请调整后重新操作。'
    except Exception as exc:
        conn.logger.bind(tag=__name__).warning('任务操作未确认: {}',type(exc).__name__)
        if (getattr(conn,'task_pending',None) or {}).get('kind')=='retry':
            return '这次任务操作尚未确认，请在网页核实。若要重试刚才的操作，可以说“重试”，不会自动重新提交。'
        return '任务服务暂时查不到结果，无法确认数量和状态，请稍后重新查询或在网页核实。'
