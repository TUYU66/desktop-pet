# 统一构建入口，沿用早期文件名；现在只构建Bootloader与带OTA的主应用。
# 验证工程→Keil重编译→fromelf导出BIN→校验打ZIP→合并首次安装HEX。
# 只生成文件，不自动烧录；输出必须是新目录，避免覆盖已验证固件。
"""Build Bootloader, OTA application and factory HEX (legacy entry name)."""
import argparse
import json
import re
from datetime import date
from pathlib import Path
import subprocess

from prepare_projects import prepare, APP
from package_firmware import package
from make_factory import factory


# -r是重编译，-o保存构建日志，没有-f烧录选项。
# 等待Keil退出并检查退出码，非零就中断发布，不用旧产物冒充新编译结果。
def build(uv4, project, log):
    # CLI build only: -r rebuilds; -f (flash) is never used.
    process = subprocess.run([str(uv4), "-r", str(project), "-j0", "-o", str(log)],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if process.returncode != 0:
        raise RuntimeError(f"Keil build failed ({process.returncode}); see {log}")


# AXF带链接/调试信息，fromelf导出实际装载字节为BIN。
# 若未得到预期单个文件则拒绝继续，防止误拿其他段或旧文件。
def export(fromelf, axf, dest):
    subprocess.run([str(fromelf), "--bin", "--output", str(dest), str(axf)], check=True)
    if not dest.is_file():
        raise RuntimeError(f"expected one raw binary: {dest}")


def next_release_version(previous, explicit=None, today=None):
    today = today or date.today()
    version = explicit if explicit is not None else max(int(today.strftime("%Y%m%d")) * 100 + 1, previous + 1)
    if not previous < version <= 0xFFFFFFFF:
        raise ValueError("release version must increase and fit uint32")
    return version


# 发布时预留递增版本并写进应用；普通Keil编译不改版本。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new directory; will not overwrite")
    parser.add_argument("--version", type=int, help="optional explicit release version, must increase")
    parser.add_argument("--keil", type=Path, default=Path("D:/Keil_v5"))
    args = parser.parse_args()
    uv4 = args.keil / "UV4/UV4.exe"
    fromelf = args.keil / "ARM/ARMCC/bin/fromelf.exe"
    if not uv4.is_file() or not fromelf.is_file():
        parser.error("Keil ARMCC5 UV4/fromelf not found; set --keil")
    version_file = Path(__file__).resolve().parents[1] / "common/ota_version.h"
    source = version_file.read_text(encoding="utf-8")
    match = re.search(r"PET_OTA_FIRMWARE_VERSION (\d+)U", source)
    if not match:
        raise ValueError("missing compiled OTA version")
    previous = int(match[1])
    version = next_release_version(previous, args.version)
    output = (args.output or Path(__file__).resolve().parents[4] / "firmware-releases/stm32" / str(version)).resolve()
    output.mkdir(parents=True, exist_ok=False)
    version_file.write_text(source[:match.start(1)] + str(version) + source[match.end(1):], encoding="utf-8")
    # 先验证主应用保留OTA入口与正确链接地址，再生成Bootloader相对路径工程。
    boot_project, ota_project = prepare()
    build(uv4, boot_project, output / "boot-build.txt")
    build(uv4, ota_project, output / "application-build.txt")
    export(fromelf, boot_project.parent / "Objects/PetBootloader.axf", output / "bootloader.bin")
    export(fromelf, ota_project.parent / "Objects/Application/Balance_Car_KEil_HAL.axf", output / "application.bin")
    # 同一个App BIN用于生成网页ZIP和首次整包，随后检查两份清单一致，避免不同镜像混包。
    manifest = package(output / "application.bin", output / "application-ota.zip", version)
    factory_text, factory_manifest = factory((output / "bootloader.bin").read_bytes(),
                                             (output / "application.bin").read_bytes(), version)
    assert manifest == factory_manifest
    (output / "factory-stage2.hex").write_text(factory_text, encoding="ascii")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "boot_bytes": (output / "bootloader.bin").stat().st_size,
                      "application_bytes": manifest["image_size"], "version": version}, indent=2))


if __name__ == "__main__":
    main()
