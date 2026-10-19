"""Local service credentials shared with Java; process environment takes priority."""
import os
import re
from pathlib import Path


def load_private_environment():
    if os.environ.get('XIAOZHI_PRIVATE_CONFIG_DISABLED') == '1':
        return
    path = Path(__file__).resolve().parents[2] / '.local' / 'application.properties'
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            key, sep, value = line.partition('=')
            if sep and re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
                os.environ.setdefault(key, value)


def expand_environment(value):
    if isinstance(value, dict):
        return {key: expand_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_environment(item) for item in value]
    if isinstance(value, str):
        return re.sub(r'\$\{([A-Z][A-Z0-9_]*)(?::([^}]*))?\}',
                      lambda match: os.environ.get(match[1], match[2] or ''), value)
    return value
