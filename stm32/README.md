# STM32 平衡车底盘

主控为 STM32F103RCT6，工程已集成 FreeRTOS（CMSIS-RTOS V2）与串口 OTA。

## 工程入口

- 主应用：`Balance_Car_KEil_HAL/MDK-ARM/Balance_Car_KEil_HAL.uvprojx`，链接地址 `0x08008000`。
- Bootloader：`Balance_Car_KEil_HAL/Bootloader/MDK-ARM/PetBootloader.uvprojx`，链接地址 `0x08000000`。
- OTA 公共协议、镜像校验和安装状态机：`Balance_Car_KEil_HAL/OTA/common/`。
- 工程内包含 HAL、CMSIS 和 FreeRTOS 依赖，Keil 使用相对路径，不依赖个人 Cube 仓库目录。

## 运行结构

FreeRTOS 分为动作、串口接收、状态监测和串口发送四个任务。姿态采样与平衡控制仍由实时中断驱动；任务通过队列和状态快照协作，不直接接管控制中断。

底盘接收 ESP32 的起立、坐下、转向和蓝牙控制请求，返回命令回执、实际姿态、电池与版本状态。已移除按键及手扶自动起立入口，由明确动作请求进入站立。

## 构建与 OTA

在项目根目录运行：

```powershell
python stm32/Balance_Car_KEil_HAL/OTA/tools/build_stage1.py
```

发布命令自动递增版本并构建 Bootloader、应用、网页 OTA ZIP 和首次安装 HEX，输出到 `firmware-releases/stm32/版本/`。普通 Keil 编译保持版本不变。

首次安装或更新 Bootloader 使用 `factory-stage2.hex`；日常应用更新上传 `application-ota.zip`。不要将应用 BIN 当作首次安装整包。操作与验证见 [网页升级使用与验收](Balance_Car_KEil_HAL/OTA/网页升级使用与验收.md)。

## 串口与供电

J13 使用 USART2，115200、8N1，通信帧包含 CRC 校验。STM32 PA2/TX2 接 ESP32 GPIO8/RX，PA3/RX2 接 GPIO9/TX，必须共地。

电脑 USB 给 ESP32 供电时断开 J13 的 5V，仅保留 GND/TX/RX，避免双电源并接。底盘 OTA 继续检查停稳状态、电压和镜像完整性。
