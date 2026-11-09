import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import make_factory as factory
import package_firmware as pack
import prepare_projects as projects
from test_ota_foundation import firmware


def app_image():
    return firmware()


class StageOneTests(unittest.TestCase):
    def test_factory_hex_addresses_checksums_and_record(self):
        boot = struct.pack("<II", 0x20000400, 0x08000009) + b"\x55" * 1000
        image = app_image()
        text, manifest = factory.factory(boot, image, 2026110301)
        decoded, base, ended = {}, 0, False
        for line in text.splitlines():
            self.assertFalse(ended)
            raw = bytes.fromhex(line[1:])
            self.assertEqual(sum(raw) & 255, 0)
            count, offset, kind = raw[0], int.from_bytes(raw[1:3], "big"), raw[3]
            self.assertEqual(len(raw), count + 5)
            payload = raw[4:-1]
            if kind == 4:
                base = int.from_bytes(payload, "big") << 16
            elif kind == 0:
                for i, value in enumerate(payload):
                    self.assertNotIn(base + offset + i, decoded)
                    decoded[base + offset + i] = value
            elif kind == 1:
                self.assertEqual(count, 0)
                ended = True
            else:
                self.fail(f"unexpected record type {kind}")
        self.assertTrue(ended)
        for start, expected in ((0x08000000, boot), (0x08008000, image),
                                (0x0803E000, factory.boot_record(manifest))):
            self.assertEqual(bytes(decoded[start + i] for i in range(len(expected))), expected)
        self.assertEqual(len(decoded), len(boot) + len(image) + 4096)
        self.assertEqual(bytes(decoded[0x0803E020 + i] for i in range(4096-32)), b'\xff' * (4096-32))
        self.assertNotIn(0x08023000, decoded)
        self.assertNotIn(0x0803F000, decoded)
        # Exercise an Intel HEX 64-KiB address boundary as a separate case.
        boundary = factory.intel_hex([(0x0800FFFE, b"1234")])
        self.assertIn(":020000040801F1", boundary)
        with self.assertRaises(ValueError):
            factory.intel_hex([(0x08000000, b"1234"), (0x08000002, b"56")])
        with self.assertRaises(ValueError):
            factory.factory(app_image(), app_image(), 1)

    def test_real_boot_record_validator(self):
        compiler = os.environ.get("CC") or shutil.which("gcc")
        self.assertTrue(compiler, "Set CC to a host C compiler")
        program = r'''
#include <assert.h>
#include <stdio.h>
#include "ota_boot_record.h"
static void set_word(unsigned char *p, uint32_t v) {
    p[0]=(unsigned char)v; p[1]=(unsigned char)(v>>8);
    p[2]=(unsigned char)(v>>16); p[3]=(unsigned char)(v>>24);
}
int main(int argc, char **argv) {
    unsigned char record[32], image[1024];
    unsigned i;
    FILE *f;
    assert(argc==3);
    f=fopen(argv[1],"rb"); assert(f); assert(fread(record,1,32,f)==32); fclose(f);
    f=fopen(argv[2],"rb"); assert(f); assert(fread(image,1,1024,f)==1024); fclose(f);
    assert(OtaBoot_Validate(record,image)==OTA_BOOT_OK);
    for(i=0;i<32;++i) {
        record[i]^=1;
        assert(OtaBoot_Validate(record,image)==OTA_BOOT_BAD_RECORD);
        record[i]^=1;
    }
    image[100]^=1;
    assert(OtaBoot_Validate(record,image)==OTA_BOOT_BAD_IMAGE);
    image[100]^=1;
    set_word(record+12,0xFFFFFFFEU);
    set_word(record+24,OtaImage_Crc32(record,24));
    /* Invalid size must be rejected BEFORE dereferencing the image pointer. */
    assert(OtaBoot_Validate(record,(const uint8_t *)1)==OTA_BOOT_BAD_RECORD);
    assert(OtaBoot_Validate(NULL,image)==OTA_BOOT_BAD_RECORD);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, binary = directory / "test.c", directory / "test.exe"
            record_path, image_path = directory / "record.bin", directory / "image.bin"
            image, manifest = pack.describe_image(app_image(), 1)
            record_path.write_bytes(factory.boot_record(manifest))
            image_path.write_bytes(image)
            source.write_text(program, encoding="utf-8")
            subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "common"), str(source),
                            str(ROOT / "common/ota_image.c"), str(ROOT / "common/ota_boot_record.c"),
                            "-o", str(binary)], check=True)
            subprocess.run([str(binary), str(record_path), str(image_path)], check=True)

    def test_project_memory_and_source_isolation(self):
        application_bytes = projects.APP_PROJECT.read_bytes()
        boot, ota = projects.prepare()
        self.assertEqual(projects.APP_PROJECT.read_bytes(), application_bytes)
        for path, start, size in ((boot, 0x08000000, 32768), (ota, 0x08008000, 110592)):
            target = ET.parse(path).find("Targets/Target")
            memory = target.find("TargetOption/TargetArmAds/ArmAdsMisc/OnChipMemories/OCR_RVCT4")
            self.assertEqual(int(memory.findtext("StartAddress"), 16), start)
            self.assertEqual(int(memory.findtext("Size"), 16), size)
            sources = target.findall("Groups/Group/Files/File")
            for source in sources:
                value = source.findtext("FilePath")
                self.assertFalse(Path(value).is_absolute(), value)
                self.assertTrue((path.parent / value).is_file(), value)
            names = [f.findtext("FileName") for f in sources]
            self.assertNotIn("system_stm32f1xx.c", names)
            if path == boot:
                self.assertNotIn("freertos.c", names)
                self.assertIn("boot_jump.s", names)
            else:
                self.assertIn("freertos.c", names)
                self.assertEqual(names.count("ota_application_system.c"), 1)


if __name__ == "__main__":
    unittest.main()
