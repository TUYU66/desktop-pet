"""Short spoken prompts; device receipts remain unchanged for verification."""

VOICE_STYLE = '''[对话表达]
像日常聊天一样说话，用简短、自然的句子直接回应。打招呼就自然打招呼，不点评用户说得短，也不固定加调侃或反问。
工具操作成功后，说清做了什么即可，不照读“已核实原创建、核实时状态、执行成功、原参数、回执、接口、版本”等流程用语。
时间用今天、明天、具体月日和上午下午来表达；跨年或日期容易混淆时保留年份。需要精确到秒时保留秒，所有提醒时间仍按北京时间理解。
多件事用“先是、还有、另外”自然连接。追问只问缺少的内容，不重复整段操作说明，不每次都要求用户说固定口令。
尚未执行、执行中、确认成功、失败和结果不确定必须分清。没有真实成功结果就不能说已经做好；不确定时直说“这次有没有做好，我还没确认，你看一下”。
不要为了口语化添加虚假的成功承诺、关系称呼或夸张情绪；诊断编号和技术细节只在用户询问时解释。
'''


def spoken_date(ms, reference_ms=None, *, include_seconds=False):
    import time
    from datetime import datetime
    from core.reminders.parser import ZONE
    from core.reminders.conversation import clock_text
    at = datetime.fromtimestamp(ms / 1000, ZONE)
    now = datetime.fromtimestamp((time.time() * 1000 if reference_ms is None else reference_ms) / 1000, ZONE)
    days = (at.date() - now.date()).days
    day = {0: '今天', 1: '明天', 2: '后天', -1: '昨天'}.get(days)
    if day is None or at.year != now.year:
        day = (f'{at.year}年' if at.year != now.year else '') + f'{at.month}月{at.day}日'
    clock_ms = ms if include_seconds else int(at.replace(second=0, microsecond=0).timestamp() * 1000)
    return day + clock_text(clock_ms)


def creation_feedback(result, body, reference_ms=None, *, checking=False, include_seconds=False):
    """Describe verified immutable creation and current state without recreating it."""
    from core.reminders.conversation import recurrence_text
    title = body['title']
    at = body['triggerAt']
    when = spoken_date(at, reference_ms, include_seconds=include_seconds)
    current = result['current']
    status = current['status']
    changed_at = current.get('scheduledAt') if body['recurrence'] == 'once' else current.get('initialAt')
    changed = ((current.get('title', title) != title)
               or (changed_at is not None and changed_at != at)
               or (current.get('recurrence', body['recurrence']) != body['recurrence']))
    if changed:
        opening = f'之前设的是{when}提醒你{title}。'
        if status in ('scheduled', 'active'):
            actual = spoken_date(changed_at or at, reference_ms, include_seconds=include_seconds)
            rule = current.get('recurrence', body['recurrence'])
            actual_time = recurrence_text(rule, changed_at or at) if rule != 'once' else actual
            return opening + f'后来改过，现在是{actual_time}提醒你{current.get("title", title)}。'
    else:
        opening = f'之前安排在{when}的“{title}”提醒，'
    if status == 'scheduled':
        return ('刚才那条已经存好了，' if checking else '好，') + f'{when}提醒你{title}。'
    if status == 'active':
        return ('刚才的周期提醒已经存好了，' if checking else '好，') + f'{recurrence_text(body["recurrence"], at)}提醒你{title}。第一次是{when}。'
    detail = {
        'dispatching': '已经存好了，正在准备提醒你。',
        'awaiting_confirmation': '已经提醒过你了，收到就跟我说一声。',
        'retry_pending': '还没收到你的回应，过会儿会再提醒你。',
        'delivery_unknown': '已经存好了，不过有没有播到我还没确认，你看一下有没有收到。',
        'completed': '已经记为收到了。',
        'cancelled': '后来取消了。',
        'expired': '已经逾期了，暂时不再自动提醒。',
        'missed': '提醒过几次还没收到回应，暂时不再自动提醒。',
        'paused': '现在暂停了，恢复后才会继续提醒。',
        'history_removed': '设置过，不过记录已经从历史里清理了。',
    }.get(status, '现在的状态我还没确认，你在日程页面看一下。')
    return opening + detail


def music_feedback(action, result):
    from core.music import PLAYBACK_MODES
    if action == 'volume': return f'好，音乐音量调到{result["volume"]}%。'
    if action == 'pause': return '好，先暂停音乐。'
    if action == 'stop': return '好，音乐停了。'
    if action == 'mode': return '好，改成' + PLAYBACK_MODES[result['mode']] + '。'
    title, artist = result.get('title', ''), result.get('artist', '')
    name = (artist+'的' if artist else '')+'《'+title+'》' if title else result.get('name', '')
    if result.get('playlistName') and action == 'play':
        mode = result.get('mode', 'sequence')
        lead = {'shuffle': '随机放', 'sequence': '按顺序放'}.get(mode, '放')
        text = f'好，{lead}你的《{result["playlistName"]}》歌单，先听{name}。'
        return text + ('这首会单曲循环。' if mode == 'single' else '')
    if result.get('playlistArtist') and action == 'play':
        return f'好，接下来放{result["playlistArtist"]}的歌，先听{("《"+title+"》") if title else name}。' + ('这首会单曲循环。' if result.get('mode') == 'single' else '')
    lead = {'next': '好，下一首放', 'previous': '好，上一首放', 'resume': '好，接着放'}.get(action, '好，接下来放')
    return lead + name + '。' if name else '好，正在准备音乐。'


def failure_feedback(raw):
    """Presentation only: keep technical receipts and exception logs intact."""
    known = {
        '服务器缺少 FFmpeg，暂时无法播放': '音乐播放还没准备好，这次放不了。你在网页里检查一下播放服务。',
        '音乐库为空，请先在网页上传 MP3': '本地还没有歌，你先在音乐空间传几首吧。',
        '没有可继续播放的音乐，请先选择歌曲': '还没有可以接着放的歌，你先点一首吧。',
        '此播放器还没有歌曲，请先选择音乐': '这里还没有选歌，你想听哪一首？',
        '请提供歌名或歌手名': '你想听哪首歌，或者哪位歌手？',
        '没有找到这首音乐，请核对歌名': '这首没找到，你再说一下歌名吧。',
        '没有找到符合条件的网易云歌曲，请核对后查询': '网易云里这次没找到，你再说一下歌名或歌手吧。',
        '网易云未找到这首歌曲': '网易云里这首没找到，你再说一下歌名吧。',
        '请先在音乐空间扫码登录网易云账号': '网易云还没登录，你先到音乐空间扫一下码吧。',
        '网易云登录已失效，请重新扫码登录': '网易云登录过期了，你重新扫一下码吧。',
        '网易云请求超时，请检查网络后重试': '网易云这次没连上，等网络恢复再试吧。',
        '网易云接口暂时不可用，请检查网络或稍后重试': '网易云这会儿没连上，过会儿再试吧。',
        '网易云未提供完整音源，请核实账号权限或歌曲状态': '这首暂时拿不到完整音源，你看一下账号权限或歌曲是否能播放。',
        '网易云仅返回试听片段，未开始完整播放，请核实账号权限': '这首现在只能试听，还没开始完整播放，你看一下账号权限吧。',
        '歌曲候选已过期，请重新说歌名或歌手查询': '刚才那组歌曲已经过了一会儿，你再说一次歌名或歌手，我重新找。',
        '歌单候选已过期，请重新说歌单名称': '刚才那组歌单已经过了一会儿，你再说一下歌单名，我重新找。',
        '还没有可播放的上一首': '前面还没有可以回放的歌。',
        '音乐暂停尚未完成，请在播放器点击停止后重试': '音乐这次还没停下来，你先在播放器点一下停止，再试吧。',
        '播放准备已被新的操作取消': '刚才那次播放已经取消了。',
        '当前播放列表已没有可用歌曲，请重新选择音乐': '这个列表已经没有能放的歌了，你再选一首吧。',
        '设备连接已关闭': '设备掉线了，连好后再试吧。',
        '设备仍在回复，请稍后重新播放': '还在说话呢，等说完再放歌吧。',
        '这个网易云歌单还没有歌曲': '这个歌单里还没有歌，你换一个吧。',
        '没有找到当前账号的红心歌单，请在网页刷新账号歌单': '这次没找到你的红心歌单，你在音乐空间刷新一下歌单吧。',
        '没有找到这个账号的歌单，请核对歌单名称': '这个歌单没找到，你再说一下歌单名吧。',
        '没有这个序号的歌曲，请选择刚才列出的版本': '刚才列出的歌里没有这个序号，你再选一下吧。',
        '没有这个序号的歌单，请选择刚才列出的歌单': '刚才列出的歌单里没有这个序号，你再选一下吧。',
        '本地只匹配到其他录音版本，请确认后播放': '本地只有其他录音版本，你想听这个版本吗？',
        '网易云音源读取失败，请检查网络或重新播放': '这首歌的音源没读到，你看看网络，过会儿再放吧。',
        'MP3 解码失败，请检查音乐文件': '这个音乐文件这次读不出来，你检查一下文件吧。',
        '音乐音量必须为 0～100 的整数': '音量可以调到0到100，你想调到多少？',
        '请选择顺序播放、随机播放或单曲循环': '想按顺序放、随机放，还是单曲循环？',
        '我没收到转向完成确认，请先看看我的状态，别连续重试。': '这次有没有转好，我还没确认。你看一下我的状态，先别重复发动作。',
        '收到指令，还没有完成确认。': '指令已经收到了，不过有没有做完，我还没确认。',
        '转向指令已发送。': '转向指令发过去了，还没确认转好。',
    }
    if raw in known: return known[raw]
    choices = {
        '本地有多个匹配：': '本地找到了几首：',
        '网易云有多个版本：': '网易云里找到了几个版本：',
        '暂未找到普通原曲，查到这些网易云版本：': '这次还没找到原曲，只有这几个版本：',
        '找到多个歌单：': '找到了几个歌单：',
    }
    for prefix, opening in choices.items():
        if raw.startswith(prefix):
            text = raw[len(prefix):]
            text = text.replace('。请说播放第几首，或在网页选择。', '。想听第几首就告诉我，也可以在音乐空间选。')
            text = text.replace('。请说播放第几首。', '。你想听第几首？')
            text = text.replace('。请说播放第几个歌单。', '。你想听哪个歌单？')
            return opening + text
    return raw


def reminder_announcement(item, index=0, count=1):
    title=item['text'].strip().rstrip('。！!；;')
    number=item['deliveryNumber']
    if count>1:
        amount={2:'两',3:'三',4:'四'}.get(count,str(count))
        opening=(f'你有{amount}件事到时间了。' if number==1 else f'还有{amount}件事等你回应。') if index==0 else ''
        lead='第一件是' if index==0 else '还有' if index==1 else '接着是'
        text=opening+lead+title+'。'
        if index==count-1: text+='收到哪一件，就告诉我那件事；想晚点再提醒，也可以跟我说。'
        return text
    lead={1:'到时间啦，',2:'再提醒你一下，',3:'我再叫你一次，'}[number]
    return lead+title+'。收到就跟我说一声，想晚点也可以。'


def chassis_acknowledgement(name, user_text=''):
    return {
        'self_chassis_stand_up':'好，这就起来。',
        'self_chassis_turn_left':'好，往左转一下。',
        'self_chassis_turn_right':'好，往右转一下。',
        'self_chassis_turn_around':'好，转个身。',
    }.get(name, '好，坐下来歇会儿。' if '坐' in user_text else '好，我靠回去歇会儿。')


def chassis_outcome(name, raw):
    # Only exact, confirmed firmware receipts can become a completion promise.
    known={
        ('self_chassis_stand_up','起立完成。'):'站起来啦。',
        ('self_chassis_rest','休息完成。'):'好，坐好了。',
        ('self_chassis_turn_left','好，我向左转过来啦。'):'好，转过来啦。',
        ('self_chassis_turn_right','好，我向右转过来啦。'):'好，转过来啦。',
        ('self_chassis_turn_around','好，我转过身来啦。'):'好，转过来了。',
    }
    return known.get((name,raw),failure_feedback(raw))
