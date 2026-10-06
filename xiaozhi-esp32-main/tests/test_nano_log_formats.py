"""Source-only guard for the ESP32's configured newlib-nano formatter."""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKENS = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|("(?:\\.|[^"\\])*")')
LONG_FORMAT = re.compile(r'(?<!%)%[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:ll|j)[diuoxX]')


class NanoLogFormatTests(unittest.TestCase):
    def test_application_formats_are_compatible_with_enabled_nano_formatter(self):
        config = (ROOT / 'sdkconfig').read_text(encoding='utf-8')
        if not re.search(r'^CONFIG_(?:LIBC_NEWLIB|NEWLIB)_NANO_FORMAT=y$', config, re.M):
            self.skipTest('Full integer formatting enabled')
        invalid = []
        for path in (ROOT / 'main').rglob('*'):
            if path.suffix not in {'.h', '.c', '.cc', '.cpp'}:
                continue
            source = path.read_text(encoding='utf-8')
            for token in TOKENS.finditer(source):
                literal = token.group(1)
                if literal and LONG_FORMAT.search(literal):
                    line = source.count('\n', 0, token.start()) + 1
                    invalid.append(f'{path.relative_to(ROOT)}:{line}: {literal}')
            uncommented = TOKENS.sub(lambda token: token.group(1) or '', source)
            if re.search(r'\b(?:PRI|SCN)[diuoxX]64\b', uncommented):
                invalid.append(f'{path.relative_to(ROOT)}: 64-bit printf/scanf macro')
        self.assertEqual(invalid, [], '\n'.join(invalid))


if __name__ == '__main__':
    unittest.main()
