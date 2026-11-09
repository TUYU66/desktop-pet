# STM32 FreeRTOS 接入与验收

## 当前实现（2026-10-27）

STM32F103RCT6，FreeRTOS V10.3.1，CMSIS-RTOS V2，Keil ARMCC 5，heap_4。
四批业务拆分已合并实现，尚待烧录后实机验收；Bootloader / OTA 尚未开发。

| 任务 | 职责 | 优先级 | 栈预算 |
| --- | --- | --- | --- |
| ActionTask（保留 StartAppTask 入口） | 唯一处理起立、坐下、转向、蓝牙会话及命令执行；产生状态和回执 | AboveNormal / 32 | 2048 B |
| ReceiveTask | 消费两个 UART 字节环形缓冲，组包、CRC 校验，复制命令入队；不执行动作 | High / 40 | 1536 B |
| MonitorTask | 10 ms 周期，电压采样和平均、低压标志、LED 和蜂鸣器 | AboveNormal / 32 | 1024 B |
| TransmitTask | 独占 USART2 与 UART5 发送；按完整帧发送，回执优先于普通遥测 | Normal / 24 | 1536 B |

内核堆由 12 KB 调整为 24 KB，涵盖四任务、队列及内核任务开销。
栈预算仍需覆盖真实动作和异常分支后根据高水位确认，不能由编译通过推断安全。

## 启动与实时边界

1. main 完成板级、DMP 和 OLED 初始化，再启动调度器；DMP 初始化仍使用主栈。
2. ActionTask 初始化蓝牙和底盘协议，发布服务就绪，然后启动原有 MPU6050 控制中断。
3. 其他任务等待就绪标志，防止在初始化完成前处理字节或发送数据。
4. MPU6050 EXTI 优先级仍为 2，继续读取姿态、执行 PID 和输出 PWM；本轮不迁移平衡闭环。
5. USART2/UART5 接收中断优先级仍为 1，仅收字节、记录硬件错误、必要时使输入失效。
   高于内核系统调用阈值，禁止调用任何 FreeRTOS API，包括 FromISR。
6. TIM7 仍是 HAL 时基及校准保护入口，优先级保持 0。
   TIM6 的 CubeMX 外设初始化保留，但不再启动业务中断；原中断回调不做 ADC 或灯鸣工作。

## 队列、取消及发送规则

- 普通命令队列 8 项，优先停止/退出队列 4 项；全部复制内容，禁止排队指向临时栈的指针。
- ActionTask 每次最多处理 4 条命令，优先消费停止/退出，随后运行原有动作完成与超时检查。
- 普通命令等待超过 500 ms 后拒绝执行；停止/退出命令不受该等待时限影响。
- 蓝牙停止、退出及输入清理使用代次编号，使此前排队的数据失效，防止退出后又执行旧方向。
  ESP32 的蓝牙退出也会使此前待处理的 ESP32 命令失效。
- 命令队列满时不执行新命令，记录计数；ESP32 请求尽可能返回 BUSY。
  队列过载、回执发送队列满或发送超时，会请求动作任务撤销蓝牙控制，旧队列不能重新开启。
- 高优先级发送队列 16 项，遥测队列 16 项。ACK、终态、电池及姿态状态优先；普通诊断可丢弃并计数。
  高队列满时最多等待 5 ms，失败会留下故障计数并撤销蓝牙控制，不能把未发出的结果当成成功确认。
- TransmitTask 是两个 UART 的唯一字节发送者；USART2 CRC 封帧也在这里完成。
  新回执可抢先于下一条遥测，不能打断已经开始发送的整帧。硬件发送等待有超时，不无限卡住任务。
- 软件环形接收缓冲为 USART2 512 B、UART5 256 B；协议格式与 PID 参数保持原样。

## 电量与监测

保留约 30 ms 采样、25 个有效样本平均（约 750 ms）、9.6 V 低压门限和 2500 ms 数据有效期。
ADC 等待移到 MonitorTask，转换失败立即标记采样无效，重新累计足够有效样本后才恢复。
任务延迟后的补调不会突发补采样；长时间停顿会丢弃旧的平均窗口。
灯和蜂鸣器沿用原 10 ms 计数节奏。硬件保护不依赖发送任务或网页收到状态。

## 日志字段

STM32 固件标识为 `DIAG,FW,2026102701,2,8191`。
诊断每约 5 秒采集并入发送队列，ESP32 校验后打印 `STM32 diagnostic:`。

```text
DIAG,RTOS,1,tick_ms,action_stack_min_B,idle_stack_min_B,timer_stack_min_B,heap_free_B,heap_min_B
DIAG,TASKS,1,tick_ms,action_stack_min_B,receive_stack_min_B,monitor_stack_min_B,transmit_stack_min_B
DIAG,IRQ,1,tick_ms,samples,period_min_us,period_max_us,execution_max_us,overruns
DIAG,QUEUE,1,command_full,command_stale,reply_full,telemetry_dropped,tx_timeout
```

- 栈值是各任务自创建以来最紧张时仍未使用的字节数，不是使用量；不包含 MSP 主栈/中断栈。
- 堆值均为字节。历史最小值不会回升，观察是否泄漏要结合当前余量及反复操作后的稳定值。
- IRQ 覆盖约 5 秒窗口，目标控制周期仍为 5 ms；耗时包含更高优先级中断的抢占。
  `overruns` 是执行达到或超过 5 ms 的次数，目标为 0。
- 队列值为累计事件次数。正常操作时 `command_full`、`reply_full`、`tx_timeout` 应为 0。
  退出时旧命令被取消可增加 `command_stale`；遥测拥塞可增加 `telemetry_dropped`，持续增长需检查发送负载。
- `DIAG,LINK,COUNTS` 第三项为 USART2 ORE，第四项为软件环形缓冲满；不是丢失命令数量。
- 同时更新 ESP32 才能正确解析新增 TASKS/QUEUE 日志；旧 ESP32 会提示未知诊断。

## 烧录后验收

先有人保护车体，在可立即断电的条件下测试。

1. 上电保持坐下，OLED 到 waiting command，网页底盘连接、电量正常。
2. 起立、重复起立、坐下各验证，回执及网页状态符合真实姿态。
3. 语音开启蓝牙、App 主动连接、重复开启、退出、再次连接，播报与控制权限一致。
4. 低速前后左右、松手停车、关闭 App/断连停车；退出后旧方向不得继续执行。
5. 移动时反复查询状态，不能因查询中断移动，也不能出现回执误判。
6. 覆盖转向、取消、受保护姿态及电量异常路径，保留原有拒绝和超时保护。
7. 连续运行约 20 分钟，保留 RTOS/TASKS/IRQ/QUEUE/LINK 日志，对照拆分前约 5 ms 周期及约 1.5 ms 最大中断耗时。
   检查全部任务栈余量、堆稳定性、无控制超周期、无异常重启和持续串口错误。

编译和主机模拟不能代替实机时序、供电及控制验收。通过后再开始 Bootloader / OTA：
独立确定 Flash 分区、应用偏移、向量表、固件校验、断电恢复及 ESP32 转发协议。

## CubeMX 再生成

.ioc 已同步四任务、各自优先级/栈及 24 KB 堆；服务初始化、队列创建、任务主体均在 USER CODE 区。
保留 LibraryCopy=1、本地 FreeRTOS 相对路径、USART2/UART5 优先级 1、自定义 RX 入口和 TIM7 保护。
重新生成后检查没有重复任务定义、重复 HAL 源或重新引入个人 Cube 仓库绝对路径，再全量编译。
需要 Keil ARMCC 5 及匹配 STM32F1 Device Pack；重新生成需要匹配 CubeF1 软件包。
