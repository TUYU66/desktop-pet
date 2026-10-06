"""Best-effort visual feedback; display failure must not affect reminder delivery."""
import asyncio
import json


async def send_face(conn, emotion):
    try:
        await asyncio.wait_for(conn.websocket.send(json.dumps({
            'type': 'llm', 'emotion': emotion, 'session_id': conn.session_id,
        })), 1)
    except Exception:
        pass
