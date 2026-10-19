"""Exercise the production inference method without loading an ASR model."""
import ast
import asyncio
import re
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Optional, Tuple, List
from unittest.mock import Mock


def method():
    path=Path(__file__).parents[1]/'core/providers/asr/fun_local.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    language=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='lang_tag_filter')
    owner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ASRProvider')
    infer=next(n for n in owner.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='speech_to_text')
    namespace=dict(asyncio=asyncio,threading=threading,time=time,re=re,Optional=Optional,Tuple=Tuple,List=List,
                   logger=Mock(),TAG='test',MAX_RETRIES=2,RETRY_DELAY=0,EMOTION_EMOJI_MAP={})
    exec(compile(ast.Module(body=[language,infer],type_ignores=[]),str(path),'exec'),namespace)
    return namespace['speech_to_text']


class AsrSerializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_round_waits_instead_of_being_dropped(self):
        started,release=threading.Event(),threading.Event()
        calls=[]
        def generate(**kwargs):
            calls.append(kwargs['input'])
            if len(calls)==1:
                started.set(); release.wait(2)
            return [{'text':'识别成功'}]
        provider=NS(_inference_lock=threading.Lock(),model=NS(generate=generate),language='zh')
        infer=method()
        first=asyncio.create_task(infer(provider,[],'one',artifacts=NS(pcm_bytes=b'one',file_path=None)))
        await asyncio.to_thread(started.wait,1)
        second=asyncio.create_task(infer(provider,[],'two',artifacts=NS(pcm_bytes=b'two',file_path=None)))
        try:
            await asyncio.sleep(.05)
            self.assertEqual([b'one'],calls)
        finally:
            release.set()
        self.assertEqual(['识别成功','识别成功'],[result[0] for result in await asyncio.gather(first,second)])
        self.assertEqual([b'one',b'two'],calls)

    async def test_cancelled_queued_round_never_runs_model(self):
        started,release=threading.Event(),threading.Event()
        calls=[]
        def generate(**kwargs):
            calls.append(kwargs['input']); started.set(); release.wait(2)
            return [{'text':'识别成功'}]
        provider=NS(_inference_lock=threading.Lock(),model=NS(generate=generate),language='zh')
        infer=method()
        first=asyncio.create_task(infer(provider,[],'one',artifacts=NS(pcm_bytes=b'one',file_path=None)))
        await asyncio.to_thread(started.wait,1)
        second=asyncio.create_task(infer(provider,[],'two',artifacts=NS(pcm_bytes=b'two',file_path=None)))
        await asyncio.sleep(.05)
        second.cancel()
        with self.assertRaises(asyncio.CancelledError): await second
        release.set(); await first
        await asyncio.sleep(.05)
        self.assertEqual([b'one'],calls)
