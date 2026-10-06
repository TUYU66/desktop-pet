import os
import yaml
import httpx
from collections.abc import Mapping


def get_project_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"


def read_config(config_path):
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config if config is not None else {}


def _load_persistent_data() -> dict:
    """加载持久化文件 data/.config.yaml"""
    path = get_project_dir() + "data/.config.yaml"
    return read_config(path)


def _save_persistent_data(data: dict):
    """保存持久化文件 data/.config.yaml"""
    path = get_project_dir() + "data/.config.yaml"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


async def load_remote_config(config: dict) -> dict:
    """启动时从 Java 后端拉取用户配置（prompt/wakeWords/voice/roles），合并到本地配置"""
    java_url = os.environ.get("JAVA_SERVER_URL", "http://localhost:8000")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{java_url}/xiaozhi/api/user/config",
                headers={"Service-Key": "xiaozhi-python"},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                if isinstance(data, dict):
                    # Empty means explicitly disable the custom phrase.
                    config["customWakeWord"] = data.get("customWakeWord", "").strip()
                    # 加载所有已知配置字段
                    for key in ("prompt", "wakeWords", "voice", "activeRole", "roles"):
                        raw = data.get(key, "").strip()
                        if raw:
                            if key == "wakeWords":
                                config["wakeup_words"] = [w.strip() for w in raw.split("\n") if w.strip()]
                                print(f"[config] 已从 Java 后端加载唤醒词: {config['wakeup_words']}")
                            else:
                                config[key] = raw
                                print(f"[config] 已从 Java 后端加载 {key}")
            else:
                print(f"[config] 从 Java 拉取配置返回 {resp.status_code}，继续使用本地配置")
    except Exception as e:
        print(f"[config] 连接 Java 后端失败 ({e})，继续使用本地配置")
    return config


def reload_config_from_java(config: dict) -> dict:
    """同步版（用于重载端点），从 Java 拉配置"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # 已在事件循环中 — 调用方应使用 await load_remote_config()
        raise RuntimeError("reload_config_from_java 不能在异步上下文中调用，请使用 await load_remote_config()")
    except RuntimeError:
        pass
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(load_remote_config(config))
    finally:
        loop.close()


def load_config() -> dict:
    """加载配置：本地 config.yaml + 持久化配置 data/.config.yaml"""
    # 加载 config.yaml 基础配置
    project_dir = get_project_dir()
    config = read_config(project_dir + "config.yaml")

    # 加载持久化配置（auth_key 等）
    persistent = _load_persistent_data()
    if persistent:
        # 合并到 config 中（persistent 优先级更高）
        _deep_merge(config, persistent)

    return config


def _deep_merge(base: dict, override: dict):
    """递归合并字典"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
