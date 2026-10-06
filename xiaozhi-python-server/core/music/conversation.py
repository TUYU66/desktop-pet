"""Local-player controls, web interruption and voice chat consent."""
import re
import time
import uuid
import json

from core.music.playback import player, pause_for_chat, PLAYBACK_MODES
from core.music.netease import song_query, liked_playlist_name


def parse_playlist_command(text):
    request = text.strip().rstrip('。！!？?').removesuffix('吧').strip()
    request = re.sub(r'^(?:(?:请|帮我|给我|麻烦你?|能不能|可以|我想要|我想))+', '', request)
    request = re.sub(r'^(?:用|在|从)?网易云(?:音乐)?(?:上|里|中)?(?:帮我)?\s*', '', request)
    kwargs = dict(source='netease')
    playback_mode = re.match(r'^(随机|顺序)(?=播放|放|听)', request)
    if playback_mode:
        kwargs['mode'] = 'shuffle' if playback_mode[1]=='随机' else 'sequence'
        request = request[playback_mode.end():]
    ordinal = re.fullmatch(r'(?:播放|听|选|就)?第?([一二三四五1-5])(?:个)?歌单', request)
    if ordinal:
        return 'play', dict(kwargs, playlist_name='第'+ordinal[1]+'个歌单')
    command = re.fullmatch(r'(?:播放|放一下|放|听一下|听)(?:一下)?\s*(.+)', request)
    if not command: return None
    value = re.sub(r'^(?:(?:我的|网易云(?:音乐)?的?))+', '', command[1].strip())
    if liked_playlist_name(value):
        return 'play', dict(kwargs, playlist_kind='liked')
    if value == '歌单':
        return 'play', dict(kwargs, playlist_name='')
    named = re.fullmatch(r'(?:我)?(?:收藏的?|创建的?)?歌单\s*(?:名叫|名为|叫)?\s*(.+)', value)
    if named:
        return 'play', dict(kwargs, playlist_name=named[1].strip())
    named = re.fullmatch(r'(.+?)歌单', value)
    if named:
        return 'play', dict(kwargs, playlist_name=named[1].strip())
    return None


def parse_song_command(text, music_context=False):
    request = text.strip().rstrip('。！!？?').removesuffix('吧').strip()
    request = re.sub(r'^(?:(?:请|帮我|给我|麻烦你?|能不能|可以|我想要|我想))+', '', request)
    source = None
    platform = re.match(r'^(?:用|在|从)?(网易云(?:音乐)?|本地)(?:上|里|中)?(?:帮我)?\s*', request)
    if platform:
        source = 'local' if platform[1] == '本地' else 'netease'
        request = request[platform.end():]
    command = re.fullmatch(r'(搜索|搜一下|搜|查找|查询|找一下|找一首|找首|找|播放歌曲|播放音乐|播放|放一首|放首|放一下|放|听一下|听一首|听)\s*(.+)', request)
    if not command: return None
    verb, value = command[1], command[2].strip()
    platform = re.match(r'^(?:用|在|从)?(网易云(?:音乐)?|本地)(?:上|里|中|的)?\s*', value)
    if platform:
        source = 'local' if platform[1] == '本地' else 'netease'
        value = value[platform.end():].strip()
    if not value: return None
    search = verb in ('搜索','搜一下','搜','查找','查询','找一下','找一首','找首','找')
    play_suffix = re.search(r'(?:然后播放|并播放|播放一下|播放|来听听|给我听)$', value) if search else None
    if play_suffix: value = value[:play_suffix.start()].strip('，, ')
    if not value: return None
    # Unrelated requests still go through the normal conversation/other tools.
    music_hint = bool(re.search(r'歌名|歌手|歌曲|的歌|这首歌|《.+》', value))
    if not source and not music_hint and re.search(r'天气|新闻|笑话|故事|提醒|日程|你说|你讲|怎么|如何|为什么', value): return None
    if search and not (source or music_hint or play_suffix or music_context): return None
    value = re.sub(r'(?:这首歌曲|这首歌)$', '', value).strip()
    if not value: return None
    if search and not play_suffix:
        kwargs = song_query(value)
        if source: kwargs['source'] = source
        return 'search', kwargs
    kwargs = dict(name=value)
    if source: kwargs['source'] = source
    return 'play', kwargs


def parse_control(text, music_context=False):
    playlist = parse_playlist_command(text)
    if playlist: return playlist
    compact = re.sub(r'[\s，。！？!?、]', '', text)
    compact = re.sub(r'^(?:请|帮我|给我|麻烦)', '', compact).removesuffix('吧')
    commands = {
        '下一首': 'next', '播放下一首': 'next', '切换下一首': 'next', '换下一首': 'next',
        '上一首': 'previous', '播放上一首': 'previous', '切换上一首': 'previous',
        '暂停': 'pause', '暂停音乐': 'pause', '暂停播放': 'pause',
        '停止音乐': 'stop', '关闭音乐': 'stop', '停止播放': 'stop',
        '继续播放': 'resume', '继续音乐': 'resume', '继续听歌': 'resume',
        '播放音乐': 'play', '放音乐': 'play', '播放歌曲': 'play',
    }
    if compact in commands:
        return commands[compact], {}
    if split_music_request(text):
        return None  # Preserve the request after the music control.
    if music_context:
        # Accept a leading imperative followed by an explanation, without
        # searching unrelated sentences or negations for control verbs.
        leading = re.split(r'[，,。！？!?；;]', text.strip(), maxsplit=1)[0]
        leading = re.sub(r'\s', '', leading)
        leading = re.sub(r'^(?:(?:请|帮我|给我|麻烦|先|暂时))+', '', leading)
        leading = re.sub(r'(?:(?:一下|一会儿|一会|吧|好吗|好不好))+$', '', leading)
        action = commands.get(leading)
        if action in ('pause', 'stop', 'resume', 'next', 'previous'):
            return action, {}
        if leading in ('不听了', '别放了', '别播放了', '下次再听'):
            return 'pause', {}
    mode_text = re.sub(r'^(?:用|在)?(?:网易云(?:音乐)?|本地)', '', compact)
    if mode_text in ('取消单曲循环', '关闭单曲循环'):
        return 'mode', dict(mode='sequence', **({'source':'local' if '本地' in compact else 'netease'} if mode_text!=compact else {}))
    mode = re.fullmatch(r'(?:(?:把|将)?(?:音乐|歌曲|这首歌)?(?:播放)?(?:模式)?)?(?:设置为|设为|切换到|切换为|改为|改成|开启)?(单曲循环|顺序播放|随机播放)(?:模式)?', mode_text)
    if mode and (mode[1] != '随机播放' or music_context or mode_text != '随机播放'):
        kwargs = dict(mode=next(key for key,value in PLAYBACK_MODES.items() if value==mode[1]))
        if mode_text != compact: kwargs['source'] = 'local' if '本地' in compact else 'netease'
        return 'mode', kwargs
    if compact in ('随机音乐', '随机播放', '随机播放音乐', '随机放首歌', '随便放首歌'):
        return 'play', {'name': 'random'}
    volume = re.fullmatch(r'(?:把)?音乐音量(?:调到|设为|调为|设置为)?(\d{1,3})(?:%|％)?', compact)
    if volume:
        return 'volume', {'volume': int(volume[1])}
    choice = re.fullmatch(r'(?:播放|听|选|就)?第?([一二三四五1-5])首(?:歌曲|歌|版本)?', compact)
    if choice: return 'play', {'name': '第'+choice[1]+'首'}
    return parse_song_command(text, music_context)


def split_music_request(text):
    """An explicit pause/stop followed by another request, not a trailing reason."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r'(.+?)[，,、\s]*(?:然后|接着|并且|并)[，,、\s]*(.+)', text.strip())
    if not match:
        return None
    command = parse_control(match[1], music_context=True)
    if command and command[0] in ('pause', 'stop'):
        return command[0], match[2].strip()
    return None


async def say(conn, text, standby=None, sentence_id=None):
    from core.handle.sendAudioHandle import send_tts_message
    from core.conversation.standby import begin_interaction, conversation_awake
    from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
    begin_interaction(conn)
    conn.sentence_id = sentence_id or str(uuid.uuid4())
    conn.task_followup_sentence = conn.sentence_id
    conn.return_to_standby = not conversation_awake(conn) if standby is None else standby
    conn.client_abort = False
    conn.client_is_speaking = True
    await send_tts_message(conn, 'start')
    for kind, content in ((SentenceType.FIRST, None), (SentenceType.MIDDLE, text), (SentenceType.LAST, None)):
        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=kind,
            content_type=ContentType.TEXT if content else ContentType.ACTION, content_detail=content))


async def handle_input(conn, text, web=False):
    """Return (handled, response/original deferred question). No LLM invents tracks."""
    from core.handle.reportHandle import enqueue_asr_report, enqueue_tts_report
    from core.conversation.standby import exit_requested
    if exit_requested(text):
        conn.music_chat_pending=None
        await pause_for_chat(conn,for_chat=False)
        return False,text  # The common chat entry queues acknowledgement + standby.
    current = getattr(conn, 'local_music', None)
    pending = getattr(conn, 'music_chat_pending', None)
    if web:
        # Web text itself requests an interaction. Do not replay an earlier
        # voice consent question when a new web request arrives.
        conn.music_chat_pending = None
        pending = None
    if pending and (time.monotonic() - pending['at'] > 120 or pending['session'] != conn.session_id):
        conn.music_chat_pending = None
        pending = None
    normalized = re.sub(r'[\s，。！？!?]', '', text)
    if (web and current and current.state in ('playing', 'loading') and normalized in
            ('不要暂停音乐', '别暂停音乐', '先不要暂停音乐', '不要停止音乐', '别停止音乐')):
        answer = '好的，继续播放音乐。'
        enqueue_asr_report(conn, text, [])
        enqueue_tts_report(conn, answer, [])
        from core.reminders.followup import deliver
        conn.task_followup_sentence = conn.sentence_id
        await deliver(conn, conn.sentence_id, text_only=True)
        from core.conversation.requests import finish_text_reply
        finish_text_reply(conn)
        return True, answer
    if pending and normalized in ('是', '好的', '好', '可以', '确认', '停止音乐并聊天', '关闭音乐进行聊天'):
        await player(conn).command('stop')
        conn.music_chat_pending = None
        return False, pending['text']
    if pending and normalized in ('不', '不用', '不要', '取消', '继续听歌'):
        conn.music_chat_pending = None
        if current and current.state == 'paused':
            text = '继续播放'
        else:
            answer = '好的，继续播放音乐。'
            enqueue_asr_report(conn, text, [])
            enqueue_tts_report(conn, answer, [])
            return True, answer
    compound = split_music_request(text)
    if web and compound:
        if current and compound[0] == 'stop':
            await player(conn).command('stop')
        else:
            await pause_for_chat(conn, for_chat=False)
        return False, text
    from core.device.chassis_intent import parse_chassis_music_request
    if parse_chassis_music_request(text):
        conn.music_chat_pending = None
        await pause_for_chat(conn, for_chat=not web)
        return False, text  # The chat entry serializes posture, outcome speech, then music.
    command = parse_control(text, music_context=bool(current and current.state in ('playing', 'loading', 'paused')))
    if command:
        conn.music_chat_pending = None
        resume_after_control = command[0] in ('volume', 'mode') and current and current.state in ('playing', 'loading')
        await pause_for_chat(conn, for_chat=not web)
        if not conn.chat_lock.acquire(blocking=False):
            tracking = getattr(conn, 'web_chat_tracking', None)
            if web and tracking:
                tracking.update('failed', '刚才那件事还没处理完，等一下再告诉我吧。')
            if not web:
                await conn.websocket.send(json.dumps({'type': 'status', 'text': '刚才那件事还没处理完，等一下再告诉我吧。'}))
            return True, '刚才那件事还没处理完，等一下再告诉我吧。'
        try:
            if web:
                tracking = getattr(conn, 'web_chat_tracking', None)
                if tracking:
                    target_session = getattr(tracking, 'fingerprint', ('', '', ''))[2]
                    if target_session and target_session != conn.session_id:
                        await conn.switch_session(target_session)
                    tracking.session = conn.session_id
            sentence = str(uuid.uuid4())
            conn.sentence_id = sentence
            tracking = getattr(conn, 'web_chat_tracking', None)
            if web and tracking:
                tracking.sentence = sentence
            action, kwargs = command
            try:
                result = await player(conn).command(action, after_sentence=sentence, **kwargs)
                if resume_after_control:
                    await player(conn).command('resume', after_sentence=sentence)
                if action == 'search': answer = search_answer(result)
                else:
                    from core.conversation.feedback import music_feedback
                    selected = result.get('players', {}).get(kwargs.get('source'), result) if action == 'mode' else result
                    answer = music_feedback(action, selected)
            except ValueError as exc:
                from core.conversation.feedback import failure_feedback
                conn.logger.bind(tag=__name__).warning('音乐操作没有完成: {}', str(exc))
                answer = failure_feedback(str(exc))
            enqueue_asr_report(conn, text, [])
            await say(conn, answer, sentence_id=sentence)
            return True, answer
        finally:
            conn.chat_lock.release()
    if web:
        await pause_for_chat(conn, for_chat=False)
        return False, text
    music_context = current and (current.state in ('playing', 'loading') or
                                 current.state == 'paused' and current.interrupted_for_chat)
    if music_context:
        conn.music_chat_pending = {'text': text, 'at': time.monotonic(), 'session': conn.session_id}
        answer = '要先停一下音乐聊一会儿吗？想继续听也可以告诉我。'
        enqueue_asr_report(conn, text, [])
        await say(conn, answer, standby=False)
        return True, answer
    return False, text


def playlist_answer(result):
    from core.conversation.feedback import music_feedback
    return music_feedback('play', result)


def search_answer(result):
    rows = result.get('results', [])
    source = '本地' if result.get('source')=='local' else '网易云'
    if not rows: return source+'这次没找到，你再说一下歌名或歌手吧。'
    return source+('找到了这首：' if len(rows)==1 else '找到了这几首：')+ '；'.join(f'第{i}首，'+(row['artist']+'的' if row.get('artist') else '')+'《'+row['title']+'》' for i,row in enumerate(rows[:5],1))+'。想听哪首就告诉我，也可以在音乐空间挑。'
