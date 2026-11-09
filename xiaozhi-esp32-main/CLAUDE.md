# ESP32 固件维护入口

当前工程固定使用 ESP32-S3、`bread-compact-wifi-lcd` 板型、简体中文资源和 WebSocket 通信。编译源文件以 `main/CMakeLists.txt` 为准，不再按照上游多板型概览推断本项目能力。

## 常用命令

在已配置的 ESP-IDF 环境执行 `idf.py build`。只有操作者明确要求时才执行 `idf.py -p PORT flash monitor`；不要擅自更换目标芯片、覆盖 sdkconfig 或重置 NVS。

## 模块入口

- `main/application.cc`：应用流程与设备状态协调。
- `main/audio/`：AFE、唤醒、录音、播放与语音处理。
- `main/display/`：LCD、字幕和 Robo Eyes 表情。
- `main/boards/bread-compact-wifi-lcd/stm32_link.cc`：底盘通信、动作回执与蓝牙协调。
- `main/led/single_led.cc`：板载状态 LED，当前板型仍在使用。
- `main/assets/`：界面和提示音资源；蓝牙提示音需与 CMake 嵌入列表配套。

具体说明见[模块文档](代码模块说明.md)、[项目说明](../README.md)和[文档索引](../docs/README.md)。保留第三方来源与许可证，硬件状态以回执和实测为准。
