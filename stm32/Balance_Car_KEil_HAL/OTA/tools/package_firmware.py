# 固件打包：读取按0x08008000链接的应用BIN，验证后生成网页上传的ZIP。
# ZIP只含manifest.json与firmware.bin；本脚本不连接设备、不执行升级。
"""Build a versioned STM32 OTA archive; no flashing or network access."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile
import zlib

LAYOUT = Path(__file__).resolve().parents[1] / "common" / "ota_layout.h"
RELEASE_MAGIC = b"PETOTAAPPV1\0"


# 检查唯一描述符及真实OTA入口，拒绝只有链接偏移、却没有升级入口的镜像。
# 12字节标识后按小端读取5个uint32；最后一个是ARM32函数指针。
# 这里的version是描述符格式号，不是用户指定的固件版本。
def validate_release(image):
    if image.count(RELEASE_MAGIC) != 1:
        raise ValueError("missing/duplicate OTA application descriptor; build the OTA-enabled Balance_Car_KEil_HAL.uvprojx")
    offset = image.index(RELEASE_MAGIC)
    if offset + 32 > len(image):
        raise ValueError("truncated OTA application descriptor")
    version, address, size, capabilities, entry = struct.unpack_from("<5I", image, offset + 12)
    base = constant("OTA_APP_BASE")
    if version not in (1, 2) or address != base or size != constant("OTA_APP_SIZE") or not capabilities & 8192 or not entry & 1 or not base + 8 <= (entry & ~1) < base + len(image):
        raise ValueError("invalid OTA application descriptor/entry; refusing non-upgradeable release")


# 从C头文件读取数值分区宏，防止PC与MCU分别维护地址后不一致；不执行任意C表达式。
def constant(name):
    # The C header is the sole source of numeric partition addresses.
    match = re.search(r"^#define\s+" + re.escape(name) + r"\s+(0x[0-9A-Fa-f]+)U$",
                      LAYOUT.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ValueError(f"missing numeric layout constant: {name}")
    return int(match[1], 16)


# 检查非零uint32版本、108KiB分区容量、初始栈和复位入口，再检查发布描述符。
# 返回规范化镜像与清单，不能通过给普通BIN加前缀冒充偏移链接。
def describe_image(raw, version):
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 0xFFFFFFFF:
        raise ValueError("version must be an integer from 1 to 4294967295")
    if len(raw) < 8 or len(raw) > constant("OTA_APP_SIZE"):
        raise ValueError("image must contain vectors and fit the 108 KiB app slot")
    # STM32F1 Flash writes halfwords. Checksums cover the exact padded bytes.
    # F1半字编程要求偶数长度，奇数BIN末尾补一个FF。
    # 清单同时记录原长和补齐长度；CRC32/SHA256/传输均覆盖补齐后的精确字节。
    image = raw + (b"\xff" if len(raw) & 1 else b"")
    stack, entry = struct.unpack_from("<II", image)
    base = constant("OTA_APP_BASE")
    if not constant("OTA_RAM_BASE") < stack <= constant("OTA_RAM_END") or stack % 8:
        raise ValueError("invalid initial stack pointer for STM32F103RC")
    if not entry & 1 or not base + 8 <= (entry & ~1) < base + len(raw):
        raise ValueError("invalid reset vector: link the application at 0x08008000 first")
    validate_release(image)
    release_offset = image.index(RELEASE_MAGIC)
    if struct.unpack_from("<I", image, release_offset + 12)[0] == 2:
        if release_offset + 36 > len(image) or struct.unpack_from("<I", image, release_offset + 32)[0] != version:
            raise ValueError("package version must match the firmware's compiled version")
    # 包版本与DIAG源码修订号不是同一字段。CRC32供Bootloader校验，SHA256供上游复验，
    # 二者都只能检验完整性，不代替发布签名。
    manifest = {
        "format": "desktop-pet-stm32-ota", "format_version": 1,
        "board": "YB-EST01-V2.0", "mcu": "STM32F103RCT6",
        "layout_version": 1, "firmware_version": version,
        "ota_application_abi": 1,
        "load_address": base, "original_size": len(raw), "image_size": len(image),
        "crc32": f"{zlib.crc32(image):08x}", "sha256": hashlib.sha256(image).hexdigest(),
    }
    return image, manifest


# 以排他模式x创建ZIP，已有文件报错而不覆盖留档。
# 固定ZIP条目的时间，减少同内容打包时仅因时间戳产生的差异。
def package(source, output, version):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError("input and output paths must differ")
    image, manifest = describe_image(source.read_bytes(), version)
    # Exclusive creation: never silently replace an existing release artifact.
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (
            ("manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()),
            ("firmware.bin", image),
        ):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return manifest


# 命令行读取参数后只生成包与清单，不接触硬件。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="raw BIN linked at 0x08008000 (not HEX/ELF)")
    parser.add_argument("output", type=Path, help="new output ZIP path")
    parser.add_argument("--version", required=True, type=int, help="monotonic uint32 version")
    args = parser.parse_args()
    try:
        manifest = package(args.input, args.output, args.version)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
