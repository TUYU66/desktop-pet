# 首次安装/恢复整包：把Bootloader、偏移应用和出厂记录编码成ST-Link可烧录的Intel HEX。
# 沿用早期phase-1描述，但格式1出厂记录仍被当前Bootloader兼容读取。
# 不是把BIN简单首尾拼接，每段数据都必须有正确的Flash装载地址。
"""Create the phase-1 SWD installation HEX from linked Bootloader/app BINs."""
import argparse
from pathlib import Path
import re
import struct
import zlib

from package_firmware import constant, describe_image

RECORD_HEADER = Path(__file__).resolve().parents[1] / "common/ota_boot_record.h"


# 直接读取出厂记录头文件的常量，保持magic/格式/提交标记与MCU一致。
def record_constant(name):
    match = re.search(r"^#define\s+" + re.escape(name) + r"\s+(0x[0-9A-Fa-f]+|[0-9]+)U$",
                      RECORD_HEADER.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ValueError(f"missing record constant: {name}")
    return int(match[1], 0)


# 生成32字节小端记录：前6字段共24字节，再追加前24字节的CRC及提交标记。
# 记录CRC保护元数据，元数据中的镜像CRC保护App实际内容。
def boot_record(manifest):
    prefix = struct.pack("<6I", record_constant("OTA_BOOT_RECORD_MAGIC"),
                         record_constant("OTA_BOOT_RECORD_FORMAT"),
                         manifest["firmware_version"], manifest["image_size"],
                         int(manifest["crc32"], 16), 0)
    return prefix + struct.pack("<2I", zlib.crc32(prefix), record_constant("OTA_BOOT_RECORD_COMMIT"))


# 一行HEX包含长度、16位地址、类型、数据和校验字节，整行字节和的低8位为0。
# 这种HEX传输格式校验与应用CRC32用途不同。
def hex_line(address, kind, data):
    record = bytes([len(data)]) + struct.pack(">H", address) + bytes([kind]) + data
    return ":" + (record + bytes([-sum(record) & 255])).hex().upper()


# 先排序并拒绝重叠，再按每行最多16字节编码。类型04切换地址高16位，00为数据，01为结束。
# 数据行不能跨64KiB边界，因此即使只剩几字节也可能要先换一个高地址记录。
def intel_hex(segments):
    lines, upper, last_end = [], None, 0
    for address, data in sorted(segments):
        if address < last_end:
            raise ValueError("overlapping HEX segments")
        last_end = address + len(data)
        offset = 0
        while offset < len(data):
            absolute = address + offset
            if absolute >> 16 != upper:
                upper = absolute >> 16
                lines.append(hex_line(0, 4, struct.pack(">H", upper)))
            size = min(16, len(data) - offset, 0x10000 - (absolute & 0xFFFF))
            lines.append(hex_line(absolute & 0xFFFF, 0, data[offset:offset + size]))
            offset += size
    lines.append(hex_line(0, 1, b""))
    return "\n".join(lines) + "\n"


# 分别检查Bootloader从0x08000000链接、App从0x08008000链接，验证栈/入口和分区容量。
# 生成记录并放在Journal，Stage不放运行镜像，首次升级时再由接收流程写入。
def factory(boot, raw_app, version):
    base = constant("OTA_FLASH_BASE")
    if not 8 <= len(boot) <= constant("OTA_BOOT_SIZE"):
        raise ValueError("Bootloader does not fit the 32 KiB boot slot")
    stack, entry = struct.unpack_from("<II", boot)
    if not constant("OTA_RAM_BASE") < stack <= constant("OTA_RAM_END") or stack % 8:
        raise ValueError("invalid Bootloader stack")
    if not entry & 1 or not base + 8 <= (entry & ~1) < base + len(boot):
        raise ValueError("Bootloader must be linked at 0x08000000")
    image, manifest = describe_image(raw_app, version)
    record = boot_record(manifest)
    # Include BOTH journal pages so a programmer erases a previous generation
    # in page one as well. A leftover newer record must not override this image.
    # 把整个两页Journal纳入HEX范围，避免重装后另一页残留更高代次的旧日志。
    # 烧录仍需采用工具正常擦除并写后校验，不能跳过擦除。
    record += b"\xff" * (constant("OTA_JOURNAL_SIZE") - len(record))
    return intel_hex([(base, boot), (constant("OTA_APP_BASE"), image),
                      (constant("OTA_JOURNAL_BASE"), record)]), manifest


# 只生成新HEX，已有输出不覆盖；本脚本不调用ST-Link擦写。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("boot", type=Path)
    parser.add_argument("application", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", type=int, required=True)
    args = parser.parse_args()
    try:
        text, _ = factory(args.boot.read_bytes(), args.application.read_bytes(), args.version)
        with args.output.open("x", encoding="ascii", newline="\n") as output:
            output.write(text)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(args.output)


if __name__ == "__main__":
    main()
