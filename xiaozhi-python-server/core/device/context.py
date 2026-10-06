"""Transient read-only state for a model turn; never a memory or wake event."""
import asyncio
import json
from concurrent.futures import TimeoutError as FutureTimeout

from core.conversation.standby import conversation_awake


async def read_snapshot(conn):
    client = getattr(conn, 'mcp_client', None)
    if client is None or not getattr(client, 'ready', False):
        return None
    from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
    for actual in ('self.dashboard.get_state', 'self.chassis.get_status'):
        name = next((key for key, value in client.name_mapping.items() if value == actual), actual)
        if not client.has_tool(name):
            continue
        try:
            # Bound readiness, transport send and result wait together. No retries
            # or movement tool calls are allowed on the context path.
            raw = await asyncio.wait_for(call_mcp_tool(conn, client, name, '{}', timeout=1), 1.2)
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict):
                return None
            return data if actual == 'self.dashboard.get_state' else {'chassis': data}
        except Exception:
            return None
    return None


def render_context(conn, data=None):
    awake = conversation_awake(conn)
    if getattr(conn, '_idle_standby_reply', False):
        voice = '正在播报空闲待命提示，语音会话仍开启；用户继续说话会取消待命'
    elif getattr(conn, 'standby_after_sentence', None):
        voice = '已接受待命指令，正在结束语音会话，不再继续聆听'
    elif isinstance(data, dict) and isinstance(data.get('conversationAwake'), bool) and data['conversationAwake'] != awake:
        voice = '服务端与设备状态尚未一致，唤醒/待命状态待确认'
    else:
        voice = '已被语音唤醒，语音会话仍开启' if awake else '待命，语音会话未开启'
    body = '姿态未知，没有本轮有效底盘数据'
    chassis = data.get('chassis') if isinstance(data, dict) else None
    if isinstance(chassis, dict) and chassis.get('connected') is True and chassis.get('motionValid') is True:
        phase = chassis.get('phase')
        moving = {'rising': '正在起身', 'resting': '正在坐下', 'rotating': '正在转向',
                  'settling_before': '正在等待站稳', 'settling_after': '正在等待站稳',
                  'waiting_audio': '动作已排队，等待播报结束'}
        if phase in moving:
            body = moving[phase] + '，动作尚未完成'
        elif chassis.get('busy') is True:
            body = '动作进行中，姿态待确认'
        elif chassis.get('posture') == 'standing':
            body = '站立，平衡控制开启；不代表完全静止'
        elif chassis.get('posture') == 'seated':
            body = '坐下，底盘已确认休息状态'
        elif chassis.get('statusValid') is True and chassis.get('balanceStopped') is True:
            body = '已停轮；停轮本身不足以确认已经坐下'
    source = '网页消息；这条消息不会唤醒机器人' if getattr(conn, 'chat_input_source', None) == 'web' else '语音对话'
    return ('【本轮设备状态，只供本轮使用，不写入长期记忆】\n'
            f'输入来源：{source}。\n语音模式：{voice}。\n身体状态：{body}。\n'
            '待命与坐下、唤醒与站立互相独立。只按当前有效设备数据回答自身状态，'
            '不要用聊天记录或刚才的承诺推断动作已经完成。未知就简短说明还没确认。'
            '只有实际语音唤醒词事件开启聆听；网页消息和网页里的唤醒词都不会开启聆听。'
            '明确待命意图结束聆听，不等于坐下或关机。回答口语简短，不照读字段名。')


def context_for_turn(conn):
    """Called from the chat worker, never from the asyncio event-loop thread."""
    data = None
    future = None
    client = getattr(conn, 'mcp_client', None)
    if client is not None and getattr(client, 'ready', False):
        try:
            future = asyncio.run_coroutine_threadsafe(read_snapshot(conn), conn.loop)
            data = future.result(timeout=1.5)
        except FutureTimeout:
            future.cancel()
        except Exception:
            if future is not None:
                future.cancel()
    return render_context(conn, data)
