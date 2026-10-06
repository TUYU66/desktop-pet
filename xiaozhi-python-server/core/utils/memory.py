import os
import sys
import importlib
from config.logger import setup_logging

logger = setup_logging()


def create_instance(class_name, *args, **kwargs):
    # 已部署的管理端可能仍下发旧配置名；入口统一切换到V2，不执行旧逻辑或失败回退。
    if class_name == "mem_local_short":
        class_name = "memory_v2"
    if os.path.exists(
        os.path.join("core", "providers", "memory", class_name, f"{class_name}.py")
    ):
        lib_name = f"core.providers.memory.{class_name}.{class_name}"
        if lib_name not in sys.modules:
            sys.modules[lib_name] = importlib.import_module(f"{lib_name}")
        return sys.modules[lib_name].MemoryProvider(*args, **kwargs)

    raise ValueError(f"不支持的记忆服务类型: {class_name}")
