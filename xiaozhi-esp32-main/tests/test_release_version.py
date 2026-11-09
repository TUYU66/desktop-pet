import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_ota', ROOT / 'scripts/build_ota.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseVersionTests(unittest.TestCase):
    def test_patch_increment_preserves_other_cmake_content(self):
        source = 'before\nset(PROJECT_VER "2.2.9")\nafter\n'
        version, result = release.next_version(source)
        self.assertEqual(version, '2.2.10')
        self.assertEqual(result, source.replace('2.2.9', '2.2.10'))
        with self.assertRaises(ValueError):
            release.next_version('set(PROJECT_VER "invalid")')

    def test_binary_version_must_come_from_app_descriptor(self):
        image = bytearray(288)
        image[0] = 0xE9
        struct.pack_into('<H', image, 12, 9)
        struct.pack_into('<I', image, 32, 0xABCD5432)
        image[48:54] = b'2.2.10'
        self.assertEqual(release.image_version(image), '2.2.10')
        image[32] ^= 1
        with self.assertRaises(ValueError):
            release.image_version(image)


if __name__ == '__main__':
    unittest.main()
