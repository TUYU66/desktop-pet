"""生成 ESP32 OTA 发布版：递增修订号、编译、核对内部版本，再导出 BIN。不会烧录。"""
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]


def next_version(source):
    match = re.search(r'^set\(PROJECT_VER "(\d+)\.(\d+)\.(\d+)"\)$', source, re.MULTILINE)
    if not match:
        raise ValueError('CMakeLists.txt 缺少三段式 PROJECT_VER')
    major, minor, patch = map(int, match.groups())
    version = f'{major}.{minor}.{patch + 1}'
    if len(version) > 31:
        raise ValueError('版本号超出 ESP-IDF 字段长度')
    return version, source[:match.start()] + f'set(PROJECT_VER "{version}")' + source[match.end():]


def image_version(image):
    if len(image) < 288 or image[0] != 0xE9 or struct.unpack_from('<H', image, 12)[0] != 9 or struct.unpack_from('<I', image, 32)[0] != 0xABCD5432:
        raise ValueError('构建结果不是 ESP32-S3 应用 BIN')
    return image[48:80].split(b'\0', 1)[0].decode('ascii')


def main():
    cmake = PROJECT / 'CMakeLists.txt'
    version, updated = next_version(cmake.read_text(encoding='utf-8'))
    idf = Path(os.environ.get('IDF_PATH', '')) / 'tools/idf.py'
    if not idf.is_file():
        raise RuntimeError('请先在 ESP-IDF 终端激活工具链（IDF_PATH）')
    destination = PROJECT.parent / 'firmware-releases/esp32' / f'xiaozhi-{version}.bin'
    if destination.exists():
        raise FileExistsError(destination)
    # 预留版本，即使构建失败也不回用；普通 idf.py build 不会递增版本。
    cmake.write_bytes(updated.encode('utf-8'))
    print(f'ESP32 发布版本：{version}', flush=True)
    subprocess.run([sys.executable, str(idf), 'build'], cwd=PROJECT, check=True)
    binary = PROJECT / 'build/xiaozhi.bin'
    if image_version(binary.read_bytes()) != version:
        raise RuntimeError('构建结果版本不匹配，拒绝导出旧固件')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as output:
        with binary.open('rb') as source:
            shutil.copyfileobj(source, output)
    print(f'上传此文件：{destination}', flush=True)


if __name__ == '__main__':
    main()
