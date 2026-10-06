"""语音与网页文字共用日程入口；在聊天/记忆之前处理，保存回执后才说成功。"""
import re
import asyncio
import time
import uuid
from datetime import datetime
from core.reminders import client as reminder_client
from core.reminders.parser import parse_time, title_from, ZONE, number

TERMINAL={'completed','cancelled','expired','missed'}


def expects_reply(conn):
    return time.monotonic()<getattr(conn,'reminder_response_until',0)


def removed_todo_request(text):
    return bool(re.search(r'待办|(?:添加|新增|创建|记下|安排|修改|查询|查看|完成|标记|恢复|删除|取消|有哪些|有什么).{0,10}任务|任务(?:清单|列表)',text)) and not re.search(r'提醒我|叫我',text)


def relevant(text):
    from core.reminders.reply import all_ack
    if all_ack(text): return True
    if re.search(r'如果|假如|举个例子|比如说|不要创建|别创建|不要设置|别设置',text): return False
    if removed_todo_request(text): return True
    from core.reminders.edits import edit_request
    if edit_request(text): return True
    if re.search(r'(?:不要|不需要|别).{0,12}(?:提醒我|叫我)',text): return False
    return bool(re.search(r'提醒|日程|周期计划|今天.{0,8}安排|明天.{0,8}安排|叫我|收到|知道了|关掉吧|好了',text))


async def respond(conn,text):
    from core.reminders.routing import route
    return await route(conn,text,_respond)


async def _respond(conn,text):
    from core.reminders.reply import reply_candidate
    conn.schedule_operation_complete=False
    from core.reminders.routing import read_query
    query=read_query(text)
    from core.reminders.batch import handle as batch_handle, is_batch_pending
    if removed_todo_request(text):
        return '现在只安排日程提醒了。你想让我什么时候提醒你做这件事？'
    from core.reminders.edits import respond_edit, EDIT, CORRECTION, UNSAFE
    from core.reminders.creation_recovery import rejected_field
    correction=rejected_field(getattr(conn,'schedule_pending',None) or {},text)
    if UNSAFE.search(text) and (EDIT.search(text) or CORRECTION.search(text)): return None
    # A newer explicit edit owns its field answers, while the batch stays saved.
    if getattr(conn,'schedule_edit_pending',None):
        edit_answer=await respond_edit(conn,text)
        if edit_answer is not None: return edit_answer
    if is_batch_pending(conn):
        batch_answer=await batch_handle(conn,text)
        if batch_answer is not None: return batch_answer
    edit_answer=None if correction else await respond_edit(conn,text)
    if edit_answer is not None: return edit_answer
    if not is_batch_pending(conn):
        batch_answer=await batch_handle(conn,text)
        if batch_answer is not None: return batch_answer
    from core.reminders.invitation import handle as invite
    try:
        invitation_answer,text=await invite(conn,text)
        if invitation_answer is not None: return invitation_answer
    except Exception as exc:
        conn.schedule_invitation=None
        conn.logger.bind(tag=__name__).warning('日程邀请识别失败，保持原有提醒: {}',type(exc).__name__)
        return None
    pending=getattr(conn,'schedule_pending',None)
    if pending and time.monotonic()>pending['expires']:
        conn.schedule_pending=None; pending=None
    if not relevant(text) and not pending and not expects_reply(conn) and not reply_candidate(conn,text): return None
    # “叫我”也用于称呼；关键词只用于召回，不能直接授权创建/修改日程。
    if '叫我' in text and '提醒' not in text:
        from core.reminders.intent import is_reminder_request
        try:
            is_request = await is_reminder_request(conn, text, pending.get('text') if pending else None)
        except Exception as exc:
            conn.logger.bind(tag=__name__).warning('日程入口意图未确认，交回普通聊天: {}', type(exc).__name__)
            return None
        if not is_request:
            return None
    if pending and text.strip('。！ ') in ('算了','不用了','取消','取消设置'):
        conn.schedule_pending=None
        suffix=' 不过刚才那条有没有存好，还得在日程页面看一下。' if pending.get('body') else ''
        return '好，这次设置先取消，已有提醒保持原样。'+suffix
    device=conn.headers.get('device-id','')
    if not device: return '我还没连上提醒设备，连好后再帮你安排。'
    attempt=None
    try:
        snapshot=await reminder_client.state(device)
        now=snapshot['serverNow']
        # 无关回答不自动作为追问答案，也不关闭正在提醒的项目。
        if pending and not relevant(text) and not reply_candidate(conn,text) and not re.search(r'点|时|分|秒|天|月|日|号|周|上午|下午|晚上|第|取消|重试|再试',text):
            return None
        original=pending['text'] if pending else text
        combined=original+'，'+text if pending else text
        action='list' if query else pending['action'] if pending else None
        if (pending and action=='confirm' and (re.search(r'收到|知道了',text) or reply_candidate(conn,text))
                and not re.fullmatch(r'第[一二三四五六七八九十0-9]+条?',text.strip(' 。！!，,'))):
            action=None # A fresh acknowledgement can select a group, not only the old choice.
        semantic_id=None
        from core.reminders.reply import CONFIRMABLE, resolve_all, confirm_group, remember, recent
        eligible=[x for x in snapshot['items'] if x['status'] in CONFIRMABLE]
        group=resolve_all(conn,text,eligible,snapshot.get('unansweredCount',len(eligible)))
        if group and action in (None,'confirm'):
            if group['action']=='ambiguous':
                return '我没能把你说的这几条和刚才的提醒对应上。说一下提醒的事情，我就帮你一起确认。'
            ids=set(group['targetIds'])
            if not ids: return '刚才这些提醒已经没有待确认的了。'
            return await confirm_group(conn,device,[x for x in eligible if x['id'] in ids])
        if action is None and text.strip('。！!，, ') in ('收到','知道了','我知道了','已收到','确认收到','收到提醒了'):
            eligible=[x for x in snapshot['items'] if x['status'] in ('awaiting_confirmation','retry_pending','delivery_unknown','missed','expired')]
            context=recent(conn)
            addressed=eligible
            if context and context['total']==1 and len(context['items'])==1:
                addressed=[x for x in eligible if x['id']==context['items'][0]['id']]
            if addressed is eligible and snapshot.get('unansweredCount',len(eligible))>len(eligible):
                return '还有些未回应的提醒没查到，我不能替你猜是哪条。你说一下提醒的事情，或者在日程页面确认吧。'
            if len(addressed)>1:
                conn.schedule_pending=dict(action='confirm',text=text,choices=[x['id'] for x in addressed[:5]],
                    choiceVersions={x['id']:x['version'] for x in addressed[:5]},baseNow=now,expires=time.monotonic()+180)
                remember(conn,addressed[:5],snapshot.get('unansweredCount',len(addressed)))
                return '你回应的是哪一条提醒？'+'；'.join(f'第{i+1}条，{x["title"]}' for i,x in enumerate(addressed[:5]))+'。'
            if len(addressed)==1: action='confirm'; semantic_id=addressed[0]['id']
        if action is None and (expects_reply(conn) or reply_candidate(conn,text) or re.search(r'收到|知道了|好了|关掉吧|关闭提醒',text)):
            from core.reminders.intent import classify
            eligible=[x for x in snapshot['items'] if x['status'] in ('awaiting_confirmation','retry_pending','delivery_unknown','missed','expired')]
            try:
                intent=await classify(conn,text,eligible,total=snapshot.get('unansweredCount',len(eligible)))
            except Exception as exc:
                conn.logger.bind(tag=__name__).warning('提醒语义识别失败，保持提醒状态: {}',type(exc).__name__)
                return '暂时没能确认你对提醒的意思，提醒保持不变。可以再说一次，或在网页操作。'
            if intent and intent['action']=='ambiguous':
                conn.reminder_response_until=time.monotonic()+90
                return '你指的是哪一条提醒？请带上提醒的事情再说一次。'
            if intent and intent['action'] in ('confirm','snooze'):
                if intent['action']=='confirm' and intent.get('targetIds'):
                    ids=set(intent['targetIds'])
                    return await confirm_group(conn,device,[x for x in eligible if x['id'] in ids])
                action=intent['action']; semantic_id=intent['targetId']
        if action is None:
            if query: action='list'
            elif re.search(r'暂停',text): action='plan_pause'
            elif re.search(r'恢复',text): action='plan_resume'
            elif re.search(r'取消|不用提醒',text): action='cancel'
            elif re.search(r'再提醒|再叫我|晚点|延后|稍后',text): action='snooze'
            elif re.search(r'修改|改成|改到',text): action='edit'
            elif re.search(r'提醒我|叫我',text):
                action='snooze' if not title_from(text) and any(x['status']=='awaiting_confirmation' for x in snapshot['items']) else 'create'
            elif expects_reply(conn) and re.search(r'分钟|小时|点|上午|下午|晚上|明天|后天',text): action='snooze'
            else: return None
        if action=='list':
            limited=snapshot.get('itemTotal',len(snapshot['items']))>len(snapshot['items'])
            items=[x for x in snapshot['items'] if x['status'] not in TERMINAL]
            plans=[p for p in snapshot['plans'] if p['status']!='cancelled']
            only_plans=bool(re.search(r'周期|每天|每周',text)) and '提醒' not in text
            if only_plans: items=[]
            if not items and not plans: return '没有找到你要查的日程提醒。'
            if '今天' in text or '明天' in text:
                from datetime import timedelta
                target=datetime.fromtimestamp(now/1000,ZONE).date()+timedelta(days=1 if '明天' in text else 0)
                items=[x for x in items if datetime.fromtimestamp((x.get('scheduledAt') or x['dueAt'])/1000,ZONE).date()==target]
                projected=[]
                for plan in plans:
                    if plan['status']!='active': continue
                    anchor=datetime.fromtimestamp(plan.get('initialAt',plan['nextAt'])/1000,ZONE)
                    candidate=datetime.combine(target,anchor.timetz())
                    rule=plan['recurrence']
                    matches=(rule=='daily' or rule=='weekly' and candidate.weekday()==anchor.weekday()
                             or rule=='weekdays' and candidate.weekday()<5
                             or rule.startswith('weekly:') and str(candidate.isoweekday()) in rule[7:].split(','))
                    at=int(candidate.timestamp()*1000)
                    if matches and at>=plan['nextAt'] and not any(
                        x.get('planId')==plan['id'] and (x.get('originalDueAt') or x['dueAt'])==at for x in snapshot['items']
                    ): projected.append({**plan,'nextAt':at})
                plans=projected
            items.sort(key=lambda x:(x.get('scheduledAt') or x['dueAt'],x['id']))
            if not items and not plans: return '这次查到的记录里没有，不过还有些记录没查完，暂时不能确定这一天没有安排。' if limited else '这一天没有日程提醒。'
            lines=[]
            if items:
                lines.append(f'你有{len(items)}条提醒：'+'；'.join(f"{x['title']}，{date_text(x.get('scheduledAt') or x['dueAt'],now)}" for x in items[:5]))
                if len(items)>5: lines.append('还有几条，你在日程页面看一下吧')
            if plans:
                lines.append(f'另外有{len(plans)}条重复提醒：'+'；'.join(f"{p['title']}，{recurrence_text(p['recurrence'],p.get('initialAt',p['nextAt']))}，{'已暂停' if p['status']=='paused' else '下次是'+date_text(p['nextAt'],now)}" for p in plans[:5]))
                if len(plans)>5: lines.append('其余重复提醒，你在日程页面看一下吧')
            conn.schedule_operation_complete=True
            return '；'.join(lines)+'。'+('这次只查到了前200条，更多记录可以在日程页面看。' if limited else '')
        if action=='create':
            from core.reminders.receipts import verified
            title=pending.get('title','') if pending else title_from(text)
            if not title:
                conn.schedule_pending=None
                return '想提醒你做什么呀？'
            body=pending.get('body') if pending else None
            retry=text.strip(' 。！!，,') in ('重试','再试一次','重试一次','核实刚才的设置')
            if pending and pending.get('rejected'):
                if retry:
                    body=pending['rejectedBody']  # Explicit retry still has the same immutable identity.
                else:
                    from core.reminders.creation_recovery import correction_source
                    time_source,complete=correction_source(pending,text)
                    pending['correctionSource']=time_source
                    pending['baseNow']=now
                    if not complete: return '刚才那条没存好，你再告诉我哪天几点提醒吧。'
                    at,rule,question=parse_time(time_source,now)
                    if question: return question
                    body=dict(action='create',requestId=str(uuid.uuid4()),title=title,triggerAt=at,recurrence=rule)
            if body and text.strip(' 。！!，,') not in ('重试','再试一次','重试一次','核实刚才的设置'):
                if not (pending and pending.get('rejected')):
                    return '刚才那条有没有存好，我还没确认。你说“重试”让我查一下，或者先在日程页面看一下吧。'
            if not body:
                marker=re.search(r'提醒我|叫我',combined)
                content_at=combined.find(title,marker.end()) if marker else -1
                time_source=combined[:content_at]+combined[content_at+len(title):] if content_at>=0 else combined
                at,rule,question=parse_time(time_source,pending['baseNow'] if pending else now)
                if question:
                    conn.schedule_pending=dict(text=combined,action=action,title=title,baseNow=pending['baseNow'] if pending else now,expires=time.monotonic()+180)
                    return question
                body=dict(action='create',requestId=str(uuid.uuid4()),title=title,triggerAt=at,recurrence=rule)
            conn.schedule_pending=dict(text=combined,action=action,title=title,baseNow=pending['baseNow'] if pending else now,
                body=body,uncertain=bool(pending and pending.get('uncertain')),expires=time.monotonic()+1800)
            attempt=conn.schedule_pending
            result=await reminder_client.command(device,body)
            if not verified(result,body,device): raise ValueError('原创建回执不匹配')
            conn.schedule_pending=None
            conn.schedule_operation_complete=True
            from core.reminders.edits import remember
            current=result['current']
            remember(conn,'item' if body['recurrence']=='once' else 'plan',dict(id=result['id'],title=current.get('title',title)))
            from core.conversation.feedback import creation_feedback
            return creation_feedback(result, body, now, checking=bool(pending and pending.get('body')),
                                     include_seconds='秒' in combined)
        plan_scope=action.startswith('plan_') or bool(re.search(r'整个|周期|以后都|每天|每周',original))
        candidates=snapshot['plans'] if plan_scope else snapshot['items']
        candidates=[x for x in candidates if x['status'] not in ('cancelled','completed')
                    and (x['status']!='expired' or action in ('confirm','snooze','edit'))]
        if semantic_id is not None:
            candidates=[x for x in candidates if x['id']==semantic_id]
        named=[x for x in candidates if x['title'] in combined]
        if named: candidates=named
        elif not plan_scope and action in ('confirm','snooze'):
            candidates=[x for x in candidates if x['status'] in ('awaiting_confirmation','retry_pending','delivery_unknown','missed','expired')]
        selected=None
        if pending and pending.get('choices'):
            m=re.search(r'第([一二三四五六七八九十0-9]+)',text)
            if m:
                index=number(m[1])-1
                if 0<=index<len(pending['choices']): selected=next((x for x in candidates if x['id']==pending['choices'][index]),None)
        if selected is None and len(candidates)==1: selected=candidates[0]
        if selected and pending and pending.get('choiceVersions') and selected['version']!=pending['choiceVersions'].get(selected['id']):
            conn.schedule_pending=None
            return '刚才那条提醒已经变了，你再说一下要处理哪条吧。'
        if selected is None:
            if not candidates and action=='confirm': return None
            if not candidates: return '没有找到这条提醒。想把以后的重复提醒也关掉，就告诉我哪件事以后都不用提醒了。'
            conn.schedule_pending=dict(text=original,action=action,choices=[x['id'] for x in candidates[:5]],baseNow=now,expires=time.monotonic()+180)
            return '你指哪一条？'+ '；'.join(f"第{i+1}条，{x['title']}" for i,x in enumerate(candidates[:5]))+'。'
        body=dict(action=action,id=selected['id'],version=selected['version'])
        if plan_scope:
            if action=='cancel': body['action']='plan_cancel'
            elif action=='edit':
                at,rule,q=parse_time(combined,now)
                if q: return q+' 请同时带上要修改的周期计划名称。'
                body.update(action='plan_edit',triggerAt=at,title=selected['title'],recurrence=rule if rule!='once' else selected['recurrence'])
            elif not action.startswith('plan_'): return '这条会重复提醒，你想先暂停、恢复，还是以后都不提醒了？'
        elif action in ('snooze','edit'):
            at,_,question=parse_time(combined.replace(selected['title'],'',1),pending['baseNow'] if pending else now)
            if question:
                conn.schedule_pending=dict(text=combined,action=action,baseNow=pending['baseNow'] if pending else now,choices=[selected['id']],expires=time.monotonic()+180)
                return question
            body.update(triggerAt=at,title=selected['title'])
        result=await reminder_client.command(device,body)
        if not isinstance(result,dict) or result.get('id')!=selected['id']:
            raise ValueError('提醒操作回执与目标不匹配')
        if not plan_scope:
            expected='completed' if action=='confirm' else 'cancelled' if action=='cancel' else 'scheduled'
            if result.get('status')!=expected or result.get('version',-1)<=body['version']:
                raise ValueError('提醒状态回执未确认')
        conn.schedule_pending=None
        conn.schedule_operation_complete=True
        conn.reminder_response_until=0
        if plan_scope:
            state = {'plan_pause': '好，后面的提醒先暂停。', 'plan_resume': '好，后面的提醒恢复啦。',
                     'plan_cancel': '好，以后不再按这个周期提醒。'}.get(body['action'], '好，后面的周期提醒改好了。')
            return state + '已经安排好的这一次还在，想取消也可以单独告诉我。'
        if action=='confirm':
            from core.device.face import send_face
            await send_face(conn,'reminder_ack')
            return '好，这条提醒记为收到了。'
        if action=='cancel': return '好，这次就不提醒了。'
        return f"好，改到{date_text(result['dueAt'])}再提醒你。"
    except reminder_client.OperationRejected as exc:
        conn.logger.bind(tag=__name__).warning('日程操作被拒绝: code={}, creation={}, previously_unknown={}',
            exc.code,attempt is not None,bool(attempt and attempt.get('uncertain')))
        if attempt is not None:
            from core.reminders.creation_recovery import locked_refusal,rejected_text
            locked=locked_refusal(exc,attempt.get('uncertain'))
            if locked:
                attempt['uncertain']=True
                return locked
            attempt['rejected']=exc.code
            attempt['rejectedBody']=attempt.pop('body')
            return rejected_text(exc.code)
        return '这次没能处理好提醒，你先在日程页面看一下现在的安排，再告诉我要怎么处理。'
    except asyncio.CancelledError:
        if attempt is not None: attempt['uncertain']=True
        raise
    except Exception as exc:
        if attempt is not None: attempt['uncertain']=True
        conn.logger.bind(tag=__name__).warning('日程操作未确认: {}',type(exc).__name__)
        return '这次有没有设置好，我还没确认。你先在日程页面看一下，避免重复设置。'


def clock_text(ms):
    dt=datetime.fromtimestamp(ms/1000,ZONE)
    hour=dt.hour
    period='凌晨' if hour<6 else '上午' if hour<12 else '中午' if hour==12 else '下午' if hour<18 else '晚上'
    shown=hour if hour<=12 else hour-12
    value=f'{period}{shown}点'
    if dt.minute: value+=f'{dt.minute}分'
    elif dt.second: value+='零分'
    if dt.second: value+=f'{dt.second}秒'
    elif not dt.minute: value+='整'
    return value


def date_text(ms, reference_ms=None):
    from core.conversation.feedback import spoken_date
    return spoken_date(ms, reference_ms, include_seconds=True)


def recurrence_text(rule,ms):
    dt=datetime.fromtimestamp(ms/1000,ZONE)
    if rule.startswith('weekly:'):
        return '每周'+ '、'.join('一二三四五六日'[int(d)-1] for d in rule[7:].split(','))+clock_text(ms)
    label={'daily':'每天','weekdays':'周一至周五','weekly':'每周'+'一二三四五六日'[dt.weekday()]}.get(rule,'单次')
    return label+clock_text(ms)


def handle_sync(conn,text):
    if getattr(conn,'loop',None) is None: return False
    from core.reminders.invitation import candidate
    from core.reminders.reply import reply_candidate
    if not isinstance(text,str) or not (relevant(text) or getattr(conn,'schedule_pending',None) or getattr(conn,'schedule_batch_pending',None) or getattr(conn,'schedule_edit_pending',None) or getattr(conn,'schedule_control_pending',None) or getattr(conn,'schedule_invitation',None) or candidate(text) or expects_reply(conn) or reply_candidate(conn,text)): return False
    import asyncio
    from core.providers.tts.dto.dto import TTSMessageDTO,SentenceType,ContentType
    future=asyncio.run_coroutine_threadsafe(respond(conn,text),conn.loop)
    from core.conversation.cancellation import wait_chat_future, ChatTurnCancelled, chat_cancelled
    generation = getattr(conn, 'input_generation', 0)
    try: answer=wait_chat_future(future, timeout=20)
    except ChatTurnCancelled:
        return True  # Already dispatched: do not repeat it or revive its old reply.
    except Exception:
        future.cancel(); answer='日程服务暂时没有响应，请在网页检查保存结果。'
    if chat_cancelled() or generation != getattr(conn, 'input_generation', 0):
        return True
    if answer is None: return False
    conn.sentence_id=str(uuid.uuid4().hex)
    conn.task_followup_sentence = conn.sentence_id
    from core.conversation.requests import bind_sentence
    bind_sentence(conn)
    conn.client_abort=False
    # Creating, editing and answering follow-up questions all retain the wake.
    # A web-only request still must not open an otherwise idle microphone.
    from core.conversation.standby import conversation_awake
    conn.return_to_standby = not conversation_awake(conn)
    for kind,content in ((SentenceType.FIRST,None),(SentenceType.MIDDLE,answer),(SentenceType.LAST,None)):
        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id,sentence_type=kind,
            content_type=ContentType.TEXT if content else ContentType.ACTION,content_detail=content))
    return True
