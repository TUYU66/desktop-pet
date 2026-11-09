# 工程辅助：验证手工维护的主应用，然后生成独立Bootloader的Keil工程。
# 主应用仍叫Balance_Car_KEil_HAL；不覆盖用户的主应用分组和调试配置。
"""Maintain the Bootloader project from the canonical OTA application project."""
from copy import deepcopy
from pathlib import Path
import os
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.parent
APP_PROJECT = APP / "MDK-ARM/Balance_Car_KEil_HAL.uvprojx"
BOOT_PROJECT = APP / "Bootloader/MDK-ARM/PetBootloader.uvprojx"


# 计算相对uvprojx目录的路径，避免依赖本机Cube仓库绝对路径。
def relative(path, project):
    return os.path.relpath(path, project.parent).replace("\\", "/")


# 深拷贝主应用目标作为工具链/芯片设置模板，原应用XML对象不受影响。
# 去掉RTE生成层、修改输出路径并关闭继承的构建钩子，防止构建时误运行外部命令。
def single_project(name):
    tree = ET.parse(APP_PROJECT)
    targets = tree.getroot().find("Targets")
    template = next(t for t in targets if t.findtext("TargetName") == "Balance_Car_KEil_HAL")
    target = deepcopy(template)
    for child in list(targets):
        targets.remove(child)
    targets.append(target)
    target.find("TargetName").text = name
    # Local CMSIS headers are already in the repository; no generated RTE source.
    for tag in ("RTE", "LayerInfo"):
        node = tree.getroot().find(tag)
        if node is not None:
            tree.getroot().remove(node)
    common = target.find("TargetOption/TargetCommonOption")
    common.find("OutputName").text = name
    common.find("OutputDirectory").text = "Objects\\"
    common.find("ListingPath").text = "Listings\\"
    for hook in ("BeforeCompile", "BeforeMake", "AfterMake"):
        for i in (1, 2):
            common.find(f"{hook}/RunUserProg{i}").text = "0"
            common.find(f"{hook}/UserProg{i}Name").text = ""
    return tree, target


# 设置目标ROM，并让Keil根据Target生成scatter，不引用旧scatter文件。
# Keil保存时IROM可能恢复物理芯片范围，实际所选IROM1区域在OCR_RVCT4。
def memory(target, base, size):
    misc = target.find("TargetOption/TargetArmAds/ArmAdsMisc")
    for name in ("IROM", "OCR_RVCT4"):
        misc.find(f"OnChipMemories/{name}/StartAddress").text = hex(base)
        misc.find(f"OnChipMemories/{name}/Size").text = hex(size)
    # Both projects use Keil's scatter generation from target memory settings.
    linker = target.find("TargetOption/TargetArmAds/LDads")
    linker.find("umfTarg").text = "1"
    linker.find("useFile").text = "0"
    linker.find("ScatterFile").text = ""


# 写出兼容Keil的XML，保留完整结束标签；这里只生成工程描述而非固件镜像。
def save(tree, project):
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    project.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, "  ")
    tree.write(project, encoding="utf-8", xml_declaration=True, short_empty_elements=False)


# 发布门槛：检查OTA宏、入口和SystemInit包装各有一份，链接区域符合App分区。
# 不合格直接报错，不能继续生成一个缺少下次OTA入口的包。
def prepare():
    from package_firmware import constant
    # The application project is hand-maintained, including user groups and debugger settings.
    # Validate it instead of regenerating it from a retired non-OTA project.
    application = ET.parse(APP_PROJECT).find("Targets/Target")
    define = application.findtext("TargetOption/TargetArmAds/Cads/VariousControls/Define", "")
    sources = [f.findtext("FileName") for f in application.findall("Groups/Group/Files/File")]
    # uVision restores IROM to the device's physical Flash range on save.
    # OCR_RVCT4 is the selected IROM1 region used by target scatter generation.
    rom = application.find("TargetOption/TargetArmAds/ArmAdsMisc/OnChipMemories/OCR_RVCT4")
    if ("PET_OTA_APPLICATION" not in define.split(",") or
        sources.count("ota_entry.c") != 1 or sources.count("ota_application_system.c") != 1 or
        "system_stm32f1xx.c" in sources or
        int(rom.findtext("StartAddress"), 16) != constant("OTA_APP_BASE") or
        int(rom.findtext("Size"), 16) != constant("OTA_APP_SIZE")):
        raise ValueError("canonical application must retain OTA entry, system wrapper and offset layout")

    # 重新限定Bootloader的宏、头文件、32KiB分区和源文件列表。
    # 复用应用的工具链设置不代表把FreeRTOS/电机业务也链接进Bootloader。
    tree, target = single_project("PetBootloader")
    memory(target, constant("OTA_FLASH_BASE"), constant("OTA_BOOT_SIZE"))
    target.find("TargetOption/TargetArmAds/Cads/VariousControls/Define").text = "STM32F103xE"
    include = ";".join(relative(p, BOOT_PROJECT) for p in
                       (APP / "CMSIS", APP / "CMSIS/core", ROOT / "common", APP / "BSP/ChassisLink"))
    target.find("TargetOption/TargetArmAds/Cads/VariousControls/IncludePath").text = include
    target.find("TargetOption/TargetArmAds/Aads/VariousControls/IncludePath").text = ""
    groups = target.find("Groups")
    groups.clear()
    group = ET.SubElement(groups, "Group")
    ET.SubElement(group, "GroupName").text = "Bootloader"
    files = ET.SubElement(group, "Files")
    # Keil类型1为C、2为汇编。SystemInit由boot_main.c提供，
    # 所以不再加入厂商system源文件或应用包装，避免同名符号重复。
    for path, kind in ((APP / "MDK-ARM/startup_stm32f103xe.s", 2),
                       (APP / "Bootloader/boot_jump.s", 2),
                       (APP / "Bootloader/boot_main.c", 1),
                       (APP / "Bootloader/boot_flash.c", 1),
                       (ROOT / "common/ota_image.c", 1),
                       (ROOT / "common/ota_boot_record.c", 1),
                       (ROOT / "common/ota_update.c", 1),
                       (ROOT / "common/ota_protocol.c", 1)):
        node = ET.SubElement(files, "File")
        ET.SubElement(node, "FileName").text = path.name
        ET.SubElement(node, "FileType").text = str(kind)
        ET.SubElement(node, "FilePath").text = relative(path, BOOT_PROJECT)
    save(tree, BOOT_PROJECT)
    return BOOT_PROJECT, APP_PROJECT


if __name__ == "__main__":
    for project in prepare():
        print(project)
