from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.music import player

play_music_function_desc = {
    'type': 'function', 'function': {
        'name': 'play_music',
        'description': '通过聊天实际查找歌曲、点歌或控制网易云与本地音乐，必须调用本工具取得结果后才说已播放或已切换模式。只要求查找用action=search，要求查找后播放或我想听某首歌用action=play。网易云必须先扫码登录；查询可只提供用户明确说出的歌手 artist 或歌名 song_title，也可同时提供，不得补猜未提供的条件。同歌手同歌名的普通录音会合并重复专辑候选，点歌时优先原曲；存在不同歌手、现场或混音等明显差异时仍需用户选择，不得自行认定翻唱为原唱。用户回答播放第几首时传song_name=第二首等，沿用刚才候选。source=netease 控制网易云，source=local 控制本地；查找和点歌未指定来源时始终先匹配本地音乐，本地无匹配才查询网易云；不要因为正在播放网易云而默认设置source=netease。明确要求网易云或本地时才填写source，其他控制未指定来源则控制当前播放器。action=mode通过mode选择single单曲循环、sequence顺序播放或shuffle随机播放，单曲循环不拦截手动切歌。暂停、继续、切歌、停止未指定source时控制当前播放器。本地只指定歌手时循环该歌手已上传歌曲。两种播放器共用设备扬声器，切换会暂停另一播放器并保留进度。',
        'parameters': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['play', 'search', 'pause', 'resume', 'next', 'previous', 'stop', 'volume', 'mode']},
            'mode': {'type': 'string', 'enum': ['single', 'sequence', 'shuffle'], 'description': 'action=mode时必填；action=play播放网易云歌单时也可同时指定模式。用户说随机播放我的红心歌单必须传action=play、playlist_kind=liked、mode=shuffle；顺序播放传sequence，不得只播报随机却省略mode'},
            'source': {'type': 'string', 'enum': ['local', 'netease'], 'description': '仅用户明确指定来源时填写；聊天点歌省略此项以便先查本地，再查网易云'},
            'artist': {'type': 'string', 'description': '用户明确指定的歌手。网易云查询可与song_title任选一项或两项一起填写'},
            'song_title': {'type': 'string', 'description': '用户明确指定的歌名。未提供歌手时只填此项'},
            'playlist_name': {'type': 'string', 'description': 'action=play时按名称播放当前账号的网易云歌单，非平台歌单搜索。不要把歌单名填成歌名；同名候选需选择，回答第几个歌单时填第二个歌单等'},
            'playlist_kind': {'type': 'string', 'enum': ['liked'], 'description': '用户说播放我的红心歌单、我的收藏歌单、我喜欢的音乐时必须传liked，定位当前账号我喜欢的音乐，不指其他收藏歌单；不要猜昵称或歌单编号'},
            'volume': {'type': 'integer', 'minimum': 0, 'maximum': 100, 'description': 'action=volume 的音乐音量百分比，0为静音，不修改说话音量'},
            'song_name': {'type': 'string', 'description': '保留用户指定的歌手和歌名，例如周杰伦的晴天、周杰伦-晴天；只说歌手的歌时也保留歌手名。控制暂停或切歌时无需填写'},
        }, 'required': ['action']},
    },
}


@register_function('play_music', play_music_function_desc, ToolType.SYSTEM_CTL)
async def play_music(conn, song_name=None, action='play', volume=None, source=None, artist=None, song_title=None, mode=None,
        playlist_name=None, playlist_kind=None):
    try:
        state = await player(conn).command(action, name=song_name, volume=volume, after_sentence=conn.sentence_id,
            source=source, artist=artist, title=song_title, mode=mode,
            playlist_name=playlist_name, playlist_kind=playlist_kind)
        if action == 'search':
            from core.music.conversation import search_answer
            text = search_answer(state)
            return ActionResponse(Action.RESPONSE, text, text)
        if source and state.get('players'):
            state = dict(state, **state['players'][source])
        from core.conversation.feedback import music_feedback
        text = music_feedback(action, state)
        # The player waits for speech completion, not for the conversation to end.
        from core.conversation.standby import conversation_awake
        conn.return_to_standby = not conversation_awake(conn)
        return ActionResponse(Action.RESPONSE, text, text)
    except ValueError as exc:
        from core.conversation.feedback import failure_feedback
        return ActionResponse(Action.RESPONSE, str(exc), failure_feedback(str(exc)))
