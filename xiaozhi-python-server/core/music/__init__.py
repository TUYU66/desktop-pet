"""Music feature package; preserve the existing public core.music imports."""
from .library import (
    MAX_BYTES, MusicLibrary, SongNotFound, normalize,
    validate_mp3, probe_details, local_details,
)
from .playback import (
    PLAYBACK_MODES, MusicPlayer, player, pause_for_chat, prompt_music_control,
)

__all__ = [
    'MAX_BYTES', 'MusicLibrary', 'SongNotFound', 'normalize',
    'validate_mp3', 'probe_details', 'local_details',
    'PLAYBACK_MODES', 'MusicPlayer', 'player', 'pause_for_chat', 'prompt_music_control',
]
