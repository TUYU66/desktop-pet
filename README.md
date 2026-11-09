# 桌面宠物机器人

ESP32-S3 负责唤醒、收音、播音与 LCD 表情，STM32 负责平衡及运动，Python 处理语音和对话，Java 保存配置、记忆与日程，网页提供聊天和设备操作入口。

本仓库包含五个代码模块，不包含 Android App、独立 NewsNow 部署、私人配置、用户数据及构建产物。

一套服务器只服务一台机器人、一个账户。首次注册创建管理员，完成初始化后关闭注册入口。

## 实物展示

实物原型及 LCD 表情、音乐与歌词显示效果。

| LCD 表情 | 音乐与歌词显示 |
| --- | --- |
| ![机器人正面与 LCD 表情](assets/showcase/robot-face.jpg) | ![机器人播放音乐与歌词显示](assets/showcase/robot-music.jpg) |

- [查看新增项目演示视频](assets/showcase/demo-03.mp4)
- [查看实物演示视频 1](assets/showcase/demo-01.mp4)
- [查看实物演示视频 2](assets/showcase/demo-02.mp4)

## 网页展示

网页控制台提供设备状态、对话记录、长期记忆、日程提醒、音乐播放及角色配置。

![概览与设备控制台](assets/showcase/web/overview.png)

<details>
<summary>表情预览与屏幕控制</summary>

![LCD 表情预览、音量、亮度与颜色设置](assets/showcase/web/face-preview.png)

</details>

<details>
<summary>对话记录</summary>

![聊天记录与网页文字对话](assets/showcase/web/chat.png)

</details>

<details>
<summary>长期记忆</summary>

![长期记忆分类、检索与编辑](assets/showcase/web/memory.png)

</details>

<details>
<summary>日程提醒</summary>

![单次提醒创建与周期安排](assets/showcase/web/reminders.png)

</details>

<details>
<summary>音乐空间</summary>

![网易云与本地音乐、播放进度及歌词](assets/showcase/web/music.png)

</details>

<details>
<summary>桌面宠物设置</summary>

![角色名称、说话风格、音色与唤醒词配置](assets/showcase/web/settings.png)

</details>

## 项目结构

| 目录 | 内容 |
| --- | --- |
| `xiaozhi-web/` | React 19、TypeScript、Vite 网页，设备控制、聊天、记忆、提醒、音乐与表情预览 |
| `xiaozhi-java-server/` | Java 17、Spring Boot、配置持久化、日程调度与 OTA |
| `xiaozhi-python-server/` | ASR、LLM、TTS、工具调用、音乐与设备通信 |
| `xiaozhi-esp32-main/` | ESP32-S3 固件，音频、LCD 表情字幕与 STM32 串口 |
| `stm32/` | STM32 平衡、转向、电机补偿与蓝牙协议，含 Keil 工程 |

## 配置与启动

需要 Node.js 24、Java 17、Maven、MySQL、Python 3.10、FFmpeg 及 Opus 原生库。生产依赖与模型要求以各模块配置为准。当前还需安装 opuslib-next==1.1.5 与 onnxruntime==1.20.1（Windows 本机验证版本），并准备系统 Opus 库；这些生产运行依赖不应与离线核心测试依赖混淆。

### 本地配置

将 `scripts/runtime-config.example.properties` 复制到项目根目录 `.local/application.properties`。为五个内部服务 Key 分别生成随机私钥，配置仅限项目数据库的账号、密码及实际使用的和风/Tavily Key。此文件已被 Git 忽略，不上传真实值。

将 `xiaozhi-python-server/config.local.example.yaml` 复制到 `xiaozhi-python-server/data/.config.yaml`，填写自己的 DeepSeek 聊天和记忆模型密钥。此文件会覆盖基础 `config.yaml`，后者只保存默认配置。

Java 与 Python 从项目根目录 `.local/application.properties` 加载内部认证配置，进程环境变量优先。缺少 Python 内部私钥时受保护请求返回 503，错误凭据返回 401；Java 登录使用 Bearer 请求头。

Python 模型权重不随仓库分发：准备 SenseVoiceSmall 与 Silero VAD，路径见 `config.yaml`。`models/` 中保留的配置、词表和上游辅助源码不能替代完整权重。音乐和网易云登录数据需要自行准备。

### 启动

- Java：`mvn -f xiaozhi-java-server/pom.xml spring-boot:run`，默认 8000，正常完成 Liquibase 数据库迁移。
- Python：在环境中进入 `xiaozhi-python-server`，安装 `requirements.txt` 后运行 `python app.py`；WebSocket 默认 8001，内部 HTTP 默认仅监听本机 8004。
- 网页：`npm --prefix xiaozhi-web ci`，再运行 `npm --prefix xiaozhi-web run dev`，默认 5173。
- 网易云桥接：在 `xiaozhi-python-server/netease-api` 执行 `npm ci`，桥接由 Python 调用。
- ESP32：在 ESP-IDF 5.5.2 环境中进入固件目录运行 `idf.py build`。当前固定为 ESP32-S3 面包板 LCD 版本，必需的内置提示音已纳入源码；旧蓝牙固定播报已退役。
- STM32：打开 `stm32/Balance_Car_KEil_HAL/MDK-ARM/Balance_Car_KEil_HAL.uvprojx`，已集成 FreeRTOS 和 OTA，依赖均使用相对路径。独立 Bootloader 工程位于 `Bootloader/MDK-ARM/PetBootloader.uvprojx`。首次安装使用发布命令生成的 `factory-stage2.hex` 整包，普通应用 BIN 不是首次安装整包。

NewsNow 为可选外部新闻服务，本次不分发其独立部署目录。需要时自行部署后修改插件地址。

### 自动检查

```powershell
mvn -B -f xiaozhi-java-server/pom.xml test
python xiaozhi-python-server/scripts/test_core.py
npm --prefix xiaozhi-web test
npm --prefix xiaozhi-web run build
npm --prefix xiaozhi-web run lint
```

离线 Python 核心依赖见 `requirements-ci.txt`。提交时 CI 自动执行 Java、Python 核心及网页检查，不覆盖真实硬件、付费模型请求和所有遗留测试。

本轮本地核心检查：Java 169 项、Python 255 项、网页 33 项、ESP32 主机测试 16 项、STM32 运行时 15 项及 OTA 12 项通过，前端生产构建通过。lint 仍有遗留 Hook 警告。远端 CI 结果以 GitHub Actions 页面为准；这些检查不等于所有遗留测试或实机验收均已通过。

### 实机验证

验证正常上电、连续对话和打断、网页聊天不唤醒、明确与空闲待命、动作回执、蓝牙进入/退出、音乐切换、提醒回应/延后/重叠/三轮未回应、等待再提醒和逾期提示、页面隐藏与设备重连、Java/Python 断线恢复。

提醒每轮 45 秒，结束未回应后间隔 5 分钟，最多三轮。软件测试不能代替实机验收；当前不宣称以上硬件流程全部通过。

## 固件发布与更新

ESP32 在已激活的 ESP-IDF 终端运行 `python scripts/build_ota.py`（工作目录为 `xiaozhi-esp32-main`）。发布命令自动递增修订号、编译并导出 `firmware-releases/esp32/xiaozhi-版本.bin`。普通 `idf.py build` 保留版本。

STM32 在项目根目录运行 `python stm32/Balance_Car_KEil_HAL/OTA/tools/build_stage1.py`。脚本自动生成递增发布号并写入应用，输出 `firmware-releases/stm32/版本/application-ota.zip`。普通 Keil 编译保留版本。网页上传时两端都自动读取固件版本；STM32 清单版本必须与新版应用内部版本一致。

上传后可在固件列表选择“升级 ESP32”或“升级底盘”。上传本身不会立即升级；机器人下次启动也会检查已启用的新版本，优先完成 ESP32 更新，再在后续启动检查底盘。底盘没有报告真实发布版本时跳过自动升级，旧版底盘需先通过网页手动升级一次。电压、停稳和完整性校验继续生效；不会自动反复重试失败升级。

ESP32 手动升级页面在实际读到设备运行目标版本后才显示完成。升级期间保持供电；网页暂时读不到设备应答不等于升级失败，不要重复点击。

网页手动 OTA 支持进度显示：等待阶段不虚构百分比，ESP32 重新上线并确认目标版本后才显示 100%。固件列表会禁用已安装和更旧版本，升级中保持稳定供电。

## 来源与许可证

项目基于小智相关开源组件扩展，第三方许可证、来源说明和文件头按各模块原位保留；不将上游代码声明为原创。截图展示开发原型，界面可能随版本变化。
