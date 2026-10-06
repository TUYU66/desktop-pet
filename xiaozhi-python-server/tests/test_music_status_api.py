"""Playback polling stays independent of directory/metadata I/O."""
import asyncio
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core.api.music_handler import MusicHandler


class MusicStatusTests(unittest.IsolatedAsyncioTestCase):
    def handler(self):
        handler = object.__new__(MusicHandler)
        handler.library = Mock()
        online = SimpleNamespace(device_name='小兰', stop_event=threading.Event())
        offline = SimpleNamespace(stop_event=threading.Event())
        offline.stop_event.set()
        handler.ws_server = SimpleNamespace(device_handlers={'online': online, 'offline': offline})
        return handler

    async def test_status_does_not_scan_or_select_songs(self):
        handler = self.handler()
        handler.library.tracks.side_effect = AssertionError('polling scanned the directory')
        request = SimpleNamespace()
        with patch.object(MusicHandler, 'authorized', return_value=True), \
                patch('core.api.music_handler.player', return_value=SimpleNamespace(status=lambda: {'state':'playing', 'position':12})):
            response = await handler.handle_status(request)
        body = json.loads(response.text)
        self.assertEqual(['online'], [d['id'] for d in body['data']['devices']])
        self.assertEqual(12, body['data']['devices'][0]['status']['position'])
        self.assertNotIn('tracks', body['data'])
        handler.library.tracks.assert_not_called()
        handler.library.select.assert_not_called()

    async def test_library_scan_runs_outside_the_event_loop(self):
        handler = self.handler()
        loop_thread = threading.get_ident()
        scan_threads = []
        def tracks():
            scan_threads.append(threading.get_ident())
            return [{'id':'song', 'name':'音乐', '_path':'private/path'}]
        handler.library.tracks.side_effect = tracks
        request = SimpleNamespace(method='GET', match_info={})
        with patch.object(MusicHandler, 'authorized', return_value=True), \
                patch('core.api.music_handler.player', return_value=SimpleNamespace(status=lambda: {'state':'stopped'})):
            response = await handler.handle(request)
        body = json.loads(response.text)
        self.assertEqual([{'id':'song', 'name':'音乐'}], body['data']['tracks'])
        self.assertEqual(1, len(scan_threads))
        self.assertNotEqual(loop_thread, scan_threads[0])

    async def test_status_requires_the_same_service_authorization(self):
        handler = self.handler()
        with patch.object(MusicHandler, 'authorized', return_value=False):
            response = await handler.handle_status(SimpleNamespace())
        self.assertEqual(401, response.status)
        handler.library.tracks.assert_not_called()


if __name__ == '__main__':
    unittest.main()
