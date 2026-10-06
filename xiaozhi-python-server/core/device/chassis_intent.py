"""High-confidence body-motion requests that should not depend on LLM tool choice.

Ambiguous, quoted, negated, or compound requests stay with the chat model.
"""

import re


_PREFIX = re.compile(
    r"^(?:(?:小智|小兰|你|请|麻烦你|麻烦|现在|先|快点|快|可以|能不能|能|"
    r"我想让你|我希望你|我要你|让你|给我|自己|再|就|帮我|帮忙))+"
)
_ENDING = re.compile(r"^(?:(?:吧|呀|啊|啦|呢|咯|哈|一下|一会儿|一会|"
                     r"歇会儿|歇一会儿|休息一下|休息一会儿|可以吗|好不好|行吗))*$")
_REST = (
    "坐下来", "坐下", "坐回去", "坐一会儿", "坐一会", "躺下来", "躺下",
    "靠回支架", "靠回去", "靠回", "往后靠", "向后靠", "后靠", "后仰",
    "休息", "歇一会儿", "歇会儿", "歇一下",
)
_STAND = ("站起来", "站起身", "站起", "起立", "起身", "从支架上起来")
_TURN_LEFT = ("向左转", "往左转", "朝左转", "左转身", "左转")
_TURN_RIGHT = ("向右转", "往右转", "朝右转", "右转身", "右转")
_TURN_AROUND = ("向后转", "往后转", "朝后转", "转身朝后", "向后转身", "掉头", "转过身")
TURN_TOOL_NAMES = frozenset({
    "self_chassis_turn_left", "self_chassis_turn_right", "self_chassis_turn_around",
    "self.chassis.turn_left", "self.chassis.turn_right", "self.chassis.turn_around",
})
_NO_ACTION = (
    "不要", "别", "不必", "不用", "先不", "暂时不", "不想", "不能", "没法",
    "怎么", "为什么", "解释", "告诉我", "是不是", "会不会", "已经", "刚才",
    "如果", "假如", "假设", "之前", "之后", "以后", "的时候",
)


def classify_direct_chassis_request(text: str) -> str | None:
    """Return the MCP alias for an unmistakable request, otherwise None."""
    if not isinstance(text, str):
        return None
    phrase = re.sub(r"[\s，。！!？?、,~～]+", "", text)
    if not phrase or len(phrase) > 32 or any(word in phrase for word in _NO_ACTION):
        return None
    if any(mark in phrase for mark in ('"', "'", "“", "”", "‘", "’", "：", ":")):
        return None
    phrase = _PREFIX.sub("", phrase, count=1)
    matches = []
    for verb in _STAND:
        if phrase.startswith(verb) and _ENDING.fullmatch(phrase[len(verb):]):
            matches.append("self_chassis_stand_up")
    for verb in _REST:
        if phrase.startswith(verb) and _ENDING.fullmatch(phrase[len(verb):]):
            matches.append("self_chassis_rest")
    for verbs, action in (
        (("开启蓝牙控制", "打开蓝牙控制", "连接蓝牙控制", "进入蓝牙控制", "蓝牙控制", "开启蓝牙", "连接蓝牙", "开启手机遥控", "打开手机遥控", "手机遥控"), "self_chassis_bluetooth_on"),
        (("关闭蓝牙控制", "退出蓝牙控制", "断开蓝牙控制", "断开蓝牙连接", "断开蓝牙", "关闭蓝牙", "退出蓝牙", "取消蓝牙控制", "断开连接", "关闭手机遥控", "退出手机遥控"), "self_chassis_bluetooth_off"),
        (_TURN_LEFT, "self_chassis_turn_left"),
        (_TURN_RIGHT, "self_chassis_turn_right"),
        (_TURN_AROUND, "self_chassis_turn_around"),
    ):
        if any(phrase.startswith(verb) and _ENDING.fullmatch(phrase[len(verb):]) for verb in verbs):
            matches.append(action)
    return matches[0] if matches and len(set(matches)) == 1 else None


def parse_chassis_music_request(text: str) -> tuple[str, str] | None:
    """Explicit posture then play/resume, preserving the requested music action."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(
        r"(.+?)(?:[，,、\s]*(?:然后|接着|再|并且|并)?[，,、\s]*)"
        r"(播放音乐|放音乐|放首歌|放歌|继续播放音乐|继续播放|继续音乐|继续听歌|恢复播放音乐|恢复播放|恢复音乐)[吧呀啊。！!\s]*",
        text.strip(),
    )
    action = classify_direct_chassis_request(match[1]) if match else None
    if action not in {"self_chassis_stand_up", "self_chassis_rest"}:
        return None
    return action, 'resume' if match[2].startswith(('继续', '恢复')) else 'play'


def classify_chassis_music_request(text: str) -> str | None:
    request = parse_chassis_music_request(text)
    return request[0] if request else None


def classify_chassis_sequence(text: str) -> list[str] | None:
    """Exactly two explicit body commands; each keeps the existing negation guard."""
    if not isinstance(text, str):
        return None
    parts = re.split(r"(?:然后|接着|再)", text.strip())
    if len(parts) != 2:
        return None
    actions = [classify_direct_chassis_request(part) for part in parts]
    allowed = TURN_TOOL_NAMES | {"self_chassis_stand_up", "self_chassis_rest"}
    return actions if all(action in allowed for action in actions) else None
