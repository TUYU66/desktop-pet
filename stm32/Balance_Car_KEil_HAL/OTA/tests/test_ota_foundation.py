import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_firmware", ROOT / "tools/package_firmware.py")
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


def firmware(size=1024, stack=0x2000C000, entry=0x08008009):
    image=bytearray(struct.pack("<II", stack, entry) + bytes([0xA5]) * (size - 8))
    if size>=96:
        image[64:96]=pack.RELEASE_MAGIC+struct.pack('<5I',1,0x08008000,110592,8192,0x08008009)
    return bytes(image)


class PackageTests(unittest.TestCase):
    def test_offset_only_or_corrupt_release_contract_is_rejected(self):
        image=bytearray(firmware())
        image[64:96]=bytes(32)
        with self.assertRaises(ValueError):pack.describe_image(image,1)
        image=bytearray(firmware());struct.pack_into('<I',image,92,0x08000009)
        with self.assertRaises(ValueError):pack.describe_image(image,1)
        image=bytearray(firmware());image[128:160]=image[64:96]
        with self.assertRaises(ValueError):pack.describe_image(image,1)
    def test_archive_checksums_padding_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.bin"
            dest = Path(directory) / "app.zip"
            source.write_bytes(firmware(1023))
            manifest = pack.package(source, dest, 2026110301)
            with zipfile.ZipFile(dest) as archive:
                self.assertEqual(set(archive.namelist()), {"manifest.json", "firmware.bin"})
                self.assertEqual(json.loads(archive.read("manifest.json")), manifest)
                image = archive.read("firmware.bin")
                self.assertEqual(image, source.read_bytes() + b"\xff")
                self.assertEqual(manifest["crc32"], f"{zlib.crc32(image):08x}")
                self.assertEqual(manifest["sha256"], pack.hashlib.sha256(image).hexdigest())
            original = dest.read_bytes()
            with self.assertRaises(FileExistsError):
                pack.package(source, dest, 2)
            self.assertEqual(dest.read_bytes(), original)
            with self.assertRaises(ValueError):
                pack.package(source, source, 2)

    def test_vectors_version_size_and_old_application_rejected(self):
        invalid = [b"", b"1234567", firmware(110594),
                   firmware(stack=0x20000000), firmware(stack=0x2000C008),
                   firmware(stack=0x20000004), firmware(entry=0x08000101),
                   firmware(entry=0x08008008), firmware(entry=0x08008001),
                   firmware(entry=0x08008401)]
        for image in invalid:
            with self.subTest(image_size=len(image), vectors=image[:8].hex()):
                with self.assertRaises(ValueError):
                    pack.describe_image(image, 1)
        for version in (0, -1, 0x100000000, True, "1"):
            with self.assertRaises(ValueError):
                pack.describe_image(firmware(), version)
        self.assertEqual(len(pack.describe_image(firmware(110592), 1)[0]), 110592)

    def test_real_c_validation_matches_packager_and_rejects_corruption(self):
        compiler = os.environ.get("CC") or shutil.which("gcc")
        self.assertTrue(compiler, "Set CC to a host C compiler")
        program = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "ota_image.h"
int main(int argc, char **argv) {
    unsigned char *data;
    size_t size;
    FILE *file;
    unsigned long expected;
    assert(argc == 3);
    file = fopen(argv[1], "rb"); assert(file);
    assert(fseek(file, 0, SEEK_END) == 0);
    size = (size_t)ftell(file); rewind(file);
    data = (unsigned char *)malloc(size); assert(data);
    assert(fread(data, 1, size, file) == size); fclose(file);
    expected = strtoul(argv[2], NULL, 16);
    assert(OtaImage_Crc32((const uint8_t *)"123456789", 9) == 0xCBF43926U);
    assert(OtaImage_Validate(data, size, (uint32_t)expected) == OTA_IMAGE_OK);
    data[16] ^= 1;
    assert(OtaImage_Validate(data, size, (uint32_t)expected) == OTA_IMAGE_BAD_CRC);
    data[16] ^= 1;
    data[0] = 1;
    assert(OtaImage_Validate(data, size, 0) == OTA_IMAGE_BAD_STACK);
    data[0] = 0;
    data[4] &= 0xFE;
    assert(OtaImage_Validate(data, size, 0) == OTA_IMAGE_BAD_ENTRY);
    assert(OtaImage_Validate(data, OTA_APP_SIZE + 2U, 0) == OTA_IMAGE_BAD_SIZE);
    assert(OtaImage_Validate(data, size - 1U, 0) == OTA_IMAGE_BAD_SIZE);
    assert(OtaImage_Validate(NULL, size, 0) == OTA_IMAGE_BAD_ARGUMENT);
    free(data); return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, binary, image_path = directory / "test.c", directory / "test.exe", directory / "app.bin"
            source.write_text(program, encoding="utf-8")
            subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "common"), str(source),
                            str(ROOT / "common/ota_image.c"), "-o", str(binary)], check=True)
            for length in (1023, 110592):
                image, manifest = pack.describe_image(firmware(length), 1)
                image_path.write_bytes(image)
                subprocess.run([str(binary), str(image_path), manifest["crc32"]], check=True)


if __name__ == "__main__":
    unittest.main()
