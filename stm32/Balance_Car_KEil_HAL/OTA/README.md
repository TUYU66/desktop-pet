# STM32 OTA 开发准备

Bootloader 独立放在 `stm32/Balance_Car_KEil_HAL/Bootloader/`；应用 OTA 适配、公共协议和工具放在 `OTA/`：

```text
Balance_Car_KEil_HAL/
├─ Core/、APP/、BSP/       正常底盘应用
├─ MDK-ARM/
│  └─ Balance_Car_KEil_HAL.uvprojx  主应用工程（包含 OTA）
├─ Bootloader/           Bootloader 源码和独立 Keil 工程
└─ OTA/
   ├─ application/        偏移应用启动适配
   ├─ common/             分区和镜像校验
   ├─ tools/              工程生成、构建和打包
   └─ tests/              主机测试
```

第一轮启动链路已由用户烧录并确认正常运行。
第二轮已实现 USART2 分包接收、Flash 暂存、双页安装日志、完整校验后安装、
安装中断电后从暂存区重新复制，以及电脑串口发送工具。
第三轮已实现网页上传校验、管理员发起、ESP32 下载/校验/独占转发、STM32 安全复位入口和进度查询，
用户已完成烧录和网页升级验证。
主应用统一使用原名 `Balance_Car_KEil_HAL.uvprojx`，从 `0x08008000` 启动，包含 OTA 入口。
不再保留独立的非 OTA 工程与重复的 `Balance_Car_OTA.uvprojx`。
未完成：物理断电异常验收、发布签名验证；没有旧版本自动回滚。
本轮操作见 [网页升级使用与验收](网页升级使用与验收.md)。
电脑串口开发工具见 [第二轮串口升级与验收](第二轮串口升级与验收.md)。
第一轮历史记录见 [首轮烧录与验收](首轮烧录与验收.md)。

## 硬件与现状

- STM32F103RCT6：256 KiB Flash、48 KiB SRAM；高容量 F1 Flash 按 2 KiB 页规划。
- ESP32 通过 USART2（PA2 TX / PA3 RX，115200、8N1）连接底盘。
- 当前应用有四个 FreeRTOS 业务任务；平衡控制仍在 MPU6050 EXTI 中断。
- 当前链接产物 RO 约 82.94 KiB，另有已初始化 RW 数据的 Flash 装载开销。
  最终以偏移工程导出的 BIN 实际长度判断是否装得下。
- Bootloader 使用独立裸机工程；升级期间不运行平衡控制或 BLE 移动。

## 分区 v1

所有结束地址均不包含该地址，唯一数值来源为 `common/ota_layout.h`。

| 分区 | 起始 | 结束 | 大小 | 用途 |
| --- | --- | --- | --- | --- |
| Bootloader | 0x08000000 | 0x08008000 | 32 KiB | 启动、接收、安装、恢复 |
| Application | 0x08008000 | 0x08023000 | 108 KiB | 固定地址运行应用 |
| Staging | 0x08023000 | 0x0803E000 | 108 KiB | 暂存完整新镜像 |
| Journal | 0x0803E000 | 0x0803F000 | 4 KiB | 两页交替记录升级状态 |
| Reserved | 0x0803F000 | 0x08040000 | 4 KiB | 预留，OTA 禁止擦写 |

芯片 Flash 容量还需在首版 Bootloader 启动时读取容量寄存器确认；不匹配即拒绝升级。
应用和暂存区必须一样大；后续增长超过 108 KiB 时需重新评估布局或增加外部存储。
暂存区保存新镜像，**不提供旧版固件自动回滚**。

## 固件包

ZIP 包含 `manifest.json` 和 `firmware.bin`；ZIP 由 ESP32/服务端解包，
Bootloader 只处理规范化元数据与原始 BIN，不实现 ZIP。
元数据含格式/布局版本、板型、MCU、递增固件版本、装载地址、原始/实际长度、CRC32、SHA256。
奇数字节 BIN 末尾补 `0xFF`，CRC32/SHA256 均覆盖补齐后的精确字节。
CRC32 使用 IEEE 参数，与 Python `zlib.crc32` 一致，不能直接替换为 F1 默认硬件 CRC。
C 模块验证大小、栈指针、Thumb 复位入口及 CRC；调用方另行验证元数据、板型及版本。
哈希和 CRC 只能检测损坏，不能证明发布者身份；后续远程入口必须保留现有管理员鉴权，
校验受信任来源，正式开放自动升级前确定签名验证方案。

从工作区根目录执行（需先完成后续偏移应用工程；现有普通应用会被拒绝）：

```powershell
& 'D:\miniconda3\envs\xiaozhi\python.exe' stm32/Balance_Car_KEil_HAL/OTA/tools/package_firmware.py app-ota.bin app-ota.zip --version 2026110301
```

工具不会覆盖已有输出。不要将现有应用的 BIN 直接平移或填充后冒充偏移链接产物。
独立 OTA 应用构建目标：IROM 起址 `0x08008000`、大小 `0x1B000`。
`application/ota_application_system.c` 在 OTA 目标中替换厂商 system 源文件，
复用厂商实现并在 `SystemInit` 中以链接符号 `__Vectors` 设置 VTOR；主应用工程不单独编译厂商 system 源文件。
恢复使用包含 Bootloader 与应用的 factory-stage2.hex 整包。

首轮 Bootloader 只读 Journal 第一页起始的 32 字节出厂记录：
magic、格式版本、固件版本、镜像长度、镜像 CRC、保留零、前 24 字节 CRC、提交标记，
均为小端 uint32。缺失/损坏时禁止启动应用，停留安全状态并输出 BOOT 错误日志。
第二轮兼容读取该出厂记录；第一次 BEGIN 将其迁移为格式 2 双页日志。
格式 2 每页保存一份 64 字节记录，包含代次、状态、当前/候选镜像元数据和 CRC，提交标记最后写。
新记录写入另一页并读回有效后才切换，保留旧页直到下一次提交。
状态为 INSTALLED / RECEIVING / READY / INSTALLING；只有 INSTALLING 会在复位后自动重装。
RECEIVING / READY 阶段仍允许启动校验有效的旧应用；进入 INSTALLING 后禁止启动旧应用，
必须完成安装并提交 INSTALLED，或通过 ABORT 完整验证当前/已复制候选应用后退出。暂存区损坏时允许重新 BEGIN。当前恢复策略从第 0 页重新复制，暂存区一直保留。
双页都损坏或芯片容量不符时留在安全状态，通过 SWD 恢复。

## 完整 OTA 运行流程

1. 前端管理员发起升级；ESP32 下载完整固件到 PSRAM 缓存，验证包和校验值。
   现有 ESP32 OTA 入口不能直接把 STM32 包当作 ESP32 固件写入。
2. ActionTask 拒绝新运动命令，撤销 BLE 控制，完成坐下；有新鲜姿态、电量数据且确认
   停止后才进入升级。正在起立、转身、校准、姿态未知或电量不足时拒绝。
   电压准入值须结合本机带载实测确定，不能把 9.6 V 低电保护线当升级准入线。
3. 请求进入 Bootloader；以受校验的持久化标记配合系统复位。Bootloader 从最早启动阶段
   保证 PC6–PC9 电机控制输出为低，不初始化驱动或启动 PWM；实测确认驱动低电平语义。
4. Bootloader 通过 USART2 接收：HELLO → BEGIN → DATA → END → INSTALL。
   第二轮采用 256 字节载荷、十六进制文本编码、逐包 ACK，外层 CRC16；完整规范见第二轮文档。
   不能复用当前仅 128 字节的文本命令接收器直接塞二进制；另设 Bootloader 专用帧解析器。
5. BEGIN 限定板型、布局、长度、版本，禁止目标落到 Bootloader/Journal/Reserved；
   DATA 仅顺序落暂存区，重复包内容一致才幂等 ACK，未来/越界包拒绝；接收超时禁止安装。
6. 完整镜像校验后，先持久化 READY，再写 INSTALLING，才允许擦 Application。
   不允许边接收边擦唯一可运行应用。Flash 擦写会阻塞本片执行，全部在底盘停稳后的
   Bootloader 中进行，绝不在正常平衡控制期间擦写。
7. 安装逐页复制；Journal 使用两页、代次和 CRC，提交字段最后写入。只有新页记录提交且
   读回有效后才允许擦除旧页，任何时候保留一份有效状态。暂存区在完成前不得被覆盖。
8. 安装中断电：重启后根据有效日志重新校验暂存镜像并继续/重新复制；应用完整校验前禁止跳转。
   接收阶段断电：旧应用未被擦，可重新传完整包。日志全坏或状态不明时留在恢复接收模式，
   不把“未知”当作可启动应用。
9. 安装完校验应用，提交 INSTALLED 后系统复位；Bootloader 复位态校验镜像再跳转，
   清理 SysTick/NVIC、设置 VTOR/MSP，保持应用启动时中断状态符合启动代码约定。
   单独验收 Pending 中断、RTOS 启动及首次站立；不能从升级任务直接裸跳应用。
10. 应用上报固件版本及启动健康状态后前端才显示成功；超时显示启动未确认，保留重传恢复能力。

串口协议及服务器/ESP32 桥接已实现；用户已完成一次网页升级（2026110404）。新增恢复规则见网页升级使用与验收文档，断电异常验收仍需实机完成。
Bootloader 不接收 BLE 升级；手机移动功能保持现有职责。

## 后续开发批次

1. **启动基础（已确认正常启动）**：独立 Keil Bootloader、GPIO 安全态、独立偏移应用目标、正常/无效镜像跳转。
   首次安装必须使用 ST-Link/SWD，同时写入 Bootloader 和偏移应用；先保留现有整包 HEX。
2. **本地升级（代码及主机测试完成，待实机）**：Flash 范围保护、暂存接收、两页日志、安装状态机；PC 串口发包工具先验证。
3. **ESP32 桥接（代码与主机测试完成，待实机）**：下载与包校验、停稳门控、串口独占、重试/恢复、进度与结果；升级时
   挂起正常动作和遥测解析，避免两个串口写入者交织数据。
4. **服务与前端（代码与构建完成，待实机）**：区分 ESP32/STM32 固件类型、管理员上传、版本展示、进度、错误提示。

实机验收必须覆盖：旧应用恢复、偏移应用中断、拒绝站立中升级、CRC 错误、越界帧、
重复包、断线、接收断电、安装每页断电、Journal 页切换断电、无效应用拒跳、升级后
四任务与站立/坐下/BLE 回归。基础单元测试通过不等于已完成这些验收。

## 已有基础测试

```powershell
$env:CC = 'D:\Devc++\Dev-Cpp\MinGW64\bin\gcc.exe'
& 'D:\miniconda3\envs\xiaozhi\python.exe' -m unittest discover -s stm32/Balance_Car_KEil_HAL/OTA/tests -v
```

测试编译并运行实际 C 镜像/出厂记录校验模块，与 Python 包交叉验证，覆盖 CRC、奇数补齐、
地址/大小边界、旧地址应用拒绝、损坏记录、损坏镜像、HEX 分区合成、工程源文件隔离和输出防覆盖。
硬件寄存器操作、启动跳转及电机低电平安全状态仍需实机验收。

器件实现参考：[ST RM0008](https://www.st.com/resource/en/reference_manual/rm0008-stm32f101xx-stm32f102xx-stm32f103xx-stm32f105xx-and-stm32f107xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf)、
[STM32F103RC 产品资料](https://www.st.com/en/microcontrollers-microprocessors/stm32f103rc.html)。

本轮增强与最新烧录路径见 [网页升级使用与验收](网页升级使用与验收.md)。普通启动窗口为 500ms；强制进入/会话窗口仍为 30 秒。发布工具强制检查 OTA 应用描述符及真实入口。

工程整理：prepare_projects.py 只验证主应用并生成独立 Bootloader，不再覆盖主应用的手工分组。CubeMX 重新生成后需核对 OTA 宏、入口和链接偏移，脚本会拒绝非 OTA 配置。
