from datetime import date
import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_stage1 import next_release_version
from package_firmware import describe_image, RELEASE_MAGIC
from test_ota_foundation import firmware


class ReleaseVersionTests(unittest.TestCase):
    def test_date_sequence_increases_even_with_older_clock(self):
        self.assertEqual(next_release_version(2026110406, today=date(2026, 11, 9)), 2026110901)
        self.assertEqual(next_release_version(2026110901, today=date(2026, 11, 9)), 2026110902)
        self.assertEqual(next_release_version(2026110902, today=date(2026, 11, 8)), 2026110903)
        with self.assertRaises(ValueError):
            next_release_version(2026110902, explicit=2026110901)

    def test_new_descriptor_binds_package_and_compiled_versions(self):
        image = bytearray(firmware(1024))
        offset = image.index(RELEASE_MAGIC)
        struct.pack_into('<I', image, offset + 12, 2)
        struct.pack_into('<I', image, offset + 32, 2026110902)
        _, manifest = describe_image(bytes(image), 2026110902)
        self.assertEqual(manifest['firmware_version'], 2026110902)
        with self.assertRaises(ValueError):
            describe_image(bytes(image), 2026110903)


if __name__ == '__main__':
    unittest.main()
