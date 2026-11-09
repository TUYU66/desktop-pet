# PC直连串口开发/验收工具，不是网页经ESP32转发的实现。
# 使用3.3V电平USB-UART并共地，打开串口后手动复位底盘以捕获握手窗口。
# 显式运行脚本会发送真实升级命令，应先固定车体并稳定供电；单纯import不会操作硬件。
"""Local STM32 update via a 3.3-V USB-UART adapter; open port, then reset board."""
import argparse
import binascii
import json
from pathlib import Path
import secrets
import time
import zipfile

from package_firmware import constant, describe_image


# 先限制ZIP条目和大小，再读取清单与BIN，不向磁盘解压条目。
# 重新计算元数据比对板型、地址、长度、CRC/SHA，避免损坏或错误固件进入传输。
def load_package(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if sorted(names) != ["firmware.bin", "manifest.json"]:
            raise ValueError("expected exactly manifest.json and firmware.bin")
        if archive.getinfo("firmware.bin").file_size > constant("OTA_APP_SIZE") or archive.getinfo("manifest.json").file_size > 8192:
            raise ValueError("package entry too large")
        manifest = json.loads(archive.read("manifest.json"))
        image = archive.read("firmware.bin")
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    _, expected = describe_image(image, manifest.get("firmware_version"))
    for key in ("format", "format_version", "board", "mcu", "layout_version", "load_address", "image_size", "crc32", "sha256"):
        if type(manifest.get(key)) is not type(expected[key]) or manifest[key] != expected[key]:
            raise ValueError(f"invalid manifest field: {key}")
    # 原长只能等于当前偶数长度，或少一个字节且最后是FF；不能任意裁剪冒充补齐。
    original = manifest.get("original_size")
    if type(original) is not int or original not in (len(image), len(image)-1) or (original != len(image) and (original % 2 != 1 or image[-1] != 255)):
        raise ValueError("invalid original size/padding")
    return image, manifest


# 外层是@载荷*四位CRC16加换行。crc_hqx初值FFFF对应CCITT-FALSE，与MCU一致。
# 帧CRC16保护单包传输；镜像CRC32保护完整镜像，两者不能混用。
def frame(payload):
    data = payload.encode("ascii")
    return b"@" + data + f"*{binascii.crc_hqx(data, 0xffff):04X}\n".encode("ascii")


# 校验帧头、换行、长度、星号位置和CRC；坏帧返回None，等待后续有效回复。
def decode_frame(data):
    if not data.startswith(b"@") or not data.endswith(b"\n") or len(data) > 640:
        return None
    body = data[1:-1]
    if len(body) < 6 or body[-5:-4] != b"*":
        return None
    payload = body[:-5]
    try:
        if len(body[-4:]) != 4 or int(body[-4:], 16) != binascii.crc_hqx(payload, 0xffff):
            return None
        return payload.decode("ascii").split(",")
    except (ValueError, UnicodeDecodeError):
        return None


# 每个对象使用一个随机非零会话ID，新命令序号递增。
# 回复身份前4字段必须匹配，旧会话或其他序号的ACK不能确认本次请求。
class SerialUpdater:
    def __init__(self, port):
        self.port = port
        self.session = secrets.randbelow(0xFFFFFFFE) + 1
        self.sequence = 0

    # 每个新命令只生成一次序号和wire；超时重发原包，利用MCU缓存回复避免重复擦写。
    # 明确ERR立即报错；无匹配回复则超时，表示结果未知，不证明操作完全没发生。
    def request(self, command, timeout=10, retries=3):
        self.sequence += 1
        identity = ["OTA", "1", str(self.session), str(self.sequence)]
        wire = frame(",".join(identity + command))
        for _ in range(retries):
            self.port.write(wire)
            self.port.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                fields = decode_frame(self.port.readline(641))
                if not fields or fields[:4] != identity:
                    continue
                if len(fields) == 6 and fields[4] == "ERR":
                    raise RuntimeError(f"bootloader rejected {command[0]}: {fields[5]}")
                if len(fields) == 8 and fields[4] == "OK" and all(f.isascii() and f.isdigit() for f in fields[5:]):
                    return tuple(map(int, fields[5:]))
        raise TimeoutError(f"no matching reply for {command[0]}; installation outcome may be unknown")

    # HELLO→BEGIN→多次DATA→END→INSTALL→RESET。DATA确认偏移必须等于本包末尾。
    # 这里不向业务应用发OTA_ENTER，操作者须打开串口后复位，让Bootloader收到HELLO。
    def update(self, image, manifest):
        # Open before board reset. HELLO attempts use new IDs; stale replies cannot
        # acknowledge a different request. No boot-entry command is sent to an app.
        print("Port open. Keep chassis supported and reset STM32 within 40 seconds.", flush=True)
        deadline = time.monotonic() + 40
        while True:
            try:
                _, state, current = self.request(["HELLO"], timeout=0.15, retries=1)
                break
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Bootloader not found. Install the stage-2 factory HEX first, then reset STM32.")
        if current >= manifest["firmware_version"]:
            raise ValueError("package version must be greater than installed version")
        self.request(["BEGIN", manifest["board"], str(manifest["firmware_version"]),
                      str(len(image)), str(int(manifest["crc32"], 16)), str(manifest["load_address"])])
        for offset in range(0, len(image), 256):
            block = image[offset:offset+256]
            received, _, _ = self.request(["DATA", str(offset), block.hex().upper()])
            if received != offset + len(block):
                raise RuntimeError("unexpected receiver offset")
            if offset % 4096 == 0 or received == len(image):
                print(f"Transmitted {received}/{len(image)} bytes", flush=True)
        # 先完整校验Stage，再执行覆盖安装；INSTALL涉及多页擦写，等待时间更长。
        self.request(["END"])
        _, state, current = self.request(["INSTALL"], timeout=20)
        if state != 1 or current != manifest["firmware_version"]:
            raise RuntimeError("installation not confirmed")
        # 只确认安装并请求复位，不像网页链路那样等待ESP32观察新的姿态/电量遥测。
        # 若复位ACK丢失，仍可能已经复位，所以提示检查设备，不能把超时当作未执行。
        self.request(["RESET"], retries=1)
        print("Installation verified; reboot requested. Check application telemetry to confirm startup.")


# 仅显式指定包和串口后才打开端口。先验证固件包，再创建串口，避免坏包触发无意义交互。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    try:
        image, manifest = load_package(args.package)
        import serial
        with serial.Serial(args.port, baudrate=115200, timeout=0.2, write_timeout=3) as port:
            SerialUpdater(port).update(image, manifest)
    except (OSError, ValueError, RuntimeError, TimeoutError, ImportError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
