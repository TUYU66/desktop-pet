# STM32 Bootloader

Keil 工程：`MDK-ARM/PetBootloader.uvprojx`。

- `boot_main.c`：启动校验、串口收包和应用跳转。
- `boot_flash.c`：Flash 读写与范围保护。
- `boot_jump.s`：设置应用栈并跳转。

链接地址为 `0x08000000`，分区大小为 32 KiB。主应用仍使用
`../MDK-ARM/Balance_Car_KEil_HAL.uvprojx`，链接地址为 `0x08008000`。

镜像校验、安装状态机和串口协议共用 `../OTA/common/`；启动文件及 CMSIS
引用工程内的相对路径，不复制业务代码，也不运行 FreeRTOS。

统一构建与打包入口：`../OTA/tools/build_stage1.py`。
首次安装或更新 Bootloader 使用包含应用的 factory-stage2.hex 整包。
操作说明见 [网页升级使用与验收](../OTA/网页升级使用与验收.md)。
