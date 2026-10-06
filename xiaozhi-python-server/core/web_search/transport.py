"""Tavily transport selection; one request only, no cross-client retry."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess

import httpx

ENDPOINT = 'https://api.tavily.com/search'


def transport_name(config):
    name = config.get('search_transport', 'auto')
    if name == 'auto':
        return 'curl' if os.name == 'nt' else 'httpx'
    if name not in ('curl', 'httpx'):
        raise ValueError('search_transport_invalid')
    return name


async def post_search(name, key, payload, timeout=24, connect_timeout=6):
    timeout=max(8,min(float(timeout),24))
    connect_timeout=max(1,min(float(connect_timeout),timeout,8))
    if name == 'httpx':
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=connect_timeout), follow_redirects=False) as client:
            return await asyncio.wait_for(client.post(
                ENDPOINT, headers={'Authorization': 'Bearer ' + str(key)}, json=payload), timeout=timeout+1)

    executable = (str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'curl.exe')
                  if os.name == 'nt' else shutil.which('curl'))
    if not executable or not Path(executable).is_file():
        raise ValueError('curl_not_available')
    if not isinstance(key, str) or any(c in key for c in '\r\n\x00') or not key.isascii():
        raise ValueError('search_key_invalid')
    # curl config double-quoted values support escaped quotes and backslashes.
    # The JSON request itself is ASCII, with Unicode encoded inside JSON strings.
    def quote(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    config = ('header = ' + quote('Authorization: Bearer ' + key) + '\n'
              + 'header = "Content-Type: application/json"\n'
              + 'data-raw = ' + quote(json.dumps(payload, ensure_ascii=True)) + '\n')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        process = await asyncio.create_subprocess_exec(
            executable, '--disable', '--silent', '--show-error', '--proto', '=https',
            '--connect-timeout', str(connect_timeout), '--max-time', str(timeout), '--request', 'POST',
            '--url', ENDPOINT, '--write-out', '\n%{http_code} %{time_appconnect}', '--config', '-',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, **options)
    except (OSError, NotImplementedError) as exc:
        raise ValueError('curl_start_failed') from exc
    communication = asyncio.create_task(process.communicate(config.encode('ascii')))
    try:
        output, _ = await asyncio.wait_for(asyncio.shield(communication), timeout=timeout+2)
    except asyncio.TimeoutError as exc:
        raise httpx.ReadTimeout('curl_process_deadline') from exc
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # Keep a single reader alive through timeout/cancellation and drain all pipes.
        try:
            await asyncio.wait_for(asyncio.shield(communication),timeout=1)
        except asyncio.TimeoutError:
            communication.cancel()
            await asyncio.gather(communication, return_exceptions=True)
    # Do not log stderr: it can include sensitive request or environment data.
    body, separator, metadata = output.rpartition(b'\n')
    fields = metadata.split()
    if process.returncode == 28:
        # No completed TLS handshake indicates a connection-stage timeout,
        # rather than a slow search result. Do not expose curl's stderr.
        try: connected = len(fields) == 2 and float(fields[1]) > 0
        except ValueError: connected = False
        if not connected:
            raise httpx.ConnectTimeout('curl_connect_timeout')
        raise httpx.ReadTimeout('curl_timeout')
    if process.returncode:
        raise ValueError('curl_exit_' + str(process.returncode))
    status = fields[0] if len(fields) == 2 else b''
    if not separator or not status.isdigit() or not 100 <= int(status) <= 599:
        raise ValueError('curl_response_invalid')
    return httpx.Response(int(status), content=body, request=httpx.Request('POST', ENDPOINT))
