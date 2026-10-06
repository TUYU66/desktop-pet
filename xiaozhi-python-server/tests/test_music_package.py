"""Public imports and storage paths survive music package organization."""
from importlib import import_module
from pathlib import Path
import unittest
from unittest.mock import patch


class MusicPackageTests(unittest.TestCase):
    def test_public_api_uses_the_same_classes_and_player_state(self):
        public = import_module('core.music')
        library = import_module('core.music.library')
        playback = import_module('core.music.playback')
        self.assertIs(public.MusicLibrary, library.MusicLibrary)
        self.assertIs(public.MusicPlayer, playback.MusicPlayer)
        self.assertIs(public.player, playback.player)
        self.assertIs(public.pause_for_chat, playback.pause_for_chat)

    def test_default_music_and_account_paths_stay_at_the_server_root(self):
        public = import_module('core.music')
        netease = import_module('core.music.netease')
        server_root = Path(__file__).resolve().parents[1]
        with patch.object(Path, 'mkdir'):
            library = public.MusicLibrary({})
        self.assertEqual(server_root / 'music', library.root)
        self.assertEqual(server_root, netease.ROOT)


if __name__ == '__main__':
    unittest.main()
