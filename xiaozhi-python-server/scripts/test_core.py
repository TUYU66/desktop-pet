"""Offline production-path checks, isolated by module with a hard timeout.

Legacy mem_local_short fixtures and live-model scripts are deliberately separate
from this CI gate; the selected production provider is memory_v2.
"""
from pathlib import Path
import os
import subprocess
import sys

TESTS = (
    'test_internal_chat_auth', 'test_service_auth', 'test_chat_submission', 'test_chat_cancellation', 'test_chat_reporting', 'test_tool_coordinator', 'test_tool_history',
    'test_p3_stream', 'test_asr_serialization', 'test_asr_turn_lifecycle', 'test_memory_v2',
    'test_memory_v2_reliability', 'test_reminder_handler', 'test_reminder_group_reply', 'test_reminder_overlap',
    'test_reminder_intent', 'test_web_reminder_reply', 'test_task_completion',
    'test_task_followup', 'test_standby', 'test_voice_interruption_standby',
    'test_weather_context', 'test_music_status_api', 'test_music_lyrics', 'test_web_request_terminals',
)


def main():
    root = Path(__file__).resolve().parents[1]
    failed = []
    environment = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'XIAOZHI_PRIVATE_CONFIG_DISABLED': '1'}
    for name in TESTS:
        print(f'Checking {name}', flush=True)
        try:
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', name+'.py'],
                                    cwd=root, env=environment, timeout=60)
            if result.returncode: failed.append(name)
        except subprocess.TimeoutExpired:
            failed.append(name+' (timeout)')
    print('Failed modules: '+(', '.join(failed) or 'none'), flush=True)
    return bool(failed)


if __name__ == '__main__':
    raise SystemExit(main())
