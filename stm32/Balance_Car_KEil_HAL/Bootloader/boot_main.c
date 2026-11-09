/*
* Bootloader 主流程：上电/复位后先运行这里，再决定进入正常应用还是留在升级模式。
 * 阅读顺序：SystemInit（先让电机安全）→ main（读日志并恢复）→ 收包循环 → 跳转应用。
 * 这里只用复位后的 HSI 8MHz、寄存器和串口轮询，不启动 HAL、FreeRTOS 或平衡控制。
 * 分层：本文件处理硬件/帧边界；ota_protocol.c 处理命令；ota_update.c 处理安装状态。
 * 正常开机：安全输出 → 读取日志 → 校验App → 短暂等待 → 清理中断/外设 → 跳到App复位入口。
 * 升级开机：应用写备份标记并复位 → 等待命令 → 收齐到Stage → 安装到App → 再复位启动。
 * 注意：CRC 检查只能发现数据损坏，不是发布者签名；本实现也不保存可自动回滚的旧镜像。
 */
#include "stm32f1xx.h"
#include "ota_boot_record.h"
#include "ota_protocol.h"
#include "link_frame.h"
#include <stdio.h>
#include <string.h>

/* 升级状态的 RAM 副本；其中一部分来自 Flash 日志。断电后 RAM 消失，必须重新读取日志。 */
static OtaUpdate update;
static OtaProtocol protocol;
static char receive_line[OTA_PROTOCOL_LINE];

/* Startup is deliberately at reset HSI=8 MHz. No HAL, RTOS, IRQ, PWM or PLL. */
extern void Boot_Jump(uint32_t stack, uint32_t entry);

/* 最早建立电机安全态：使能 GPIOC，先把 PC6～PC9 输出锁存值清零，再改成推挽输出。
 * 这样可减少切换引脚模式时的输出毛刺；不配置 PWM，不让 Bootloader 参与平衡控制。 */
static void motors_safe(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPCEN;
    (void)RCC->APB2ENR;
    GPIOC->BRR = 0x03C0U;
    GPIOC->CRL = (GPIOC->CRL & 0x00FFFFFFU) | 0x22000000U;
    GPIOC->CRH = (GPIOC->CRH & 0xFFFFFF00U) | 0x00000022U;
}

/* Called by the vendor startup BEFORE C runtime initialization. No globals. */
/* 启动汇编在初始化 C 全局变量之前调用本函数。
 * 因此这里只能用寄存器和常量，不能依赖 update、protocol 或其他尚未初始化的全局数据。
 * VTOR 指向 Bootloader 自己的向量表；DSB/ISB 保证后续执行观察到新的寄存器配置。 */
void SystemInit(void)
{
    motors_safe();
    SCB->VTOR = OTA_BOOT_BASE;
    __DSB();
    __ISB();
}

/* USART2：PA2 为复用推挽 TX，PA3 为浮空输入 RX，115200 波特，8 数据位、无校验、1 停止位。
 * BRR=69 依据复位时的 8MHz 外设时钟计算；若改变启动时钟，必须一起检查这个分频值。 */
static void uart_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;
    GPIOA->CRL = (GPIOA->CRL & ~0x0000FF00U) | 0x00004A00U;
    USART2->BRR = 69U; /* 8 MHz / 115200, nearest divider. */
    USART2->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;
}

/* 阻塞发送调试文字或协议回复，每次等待都有次数上限，避免 UART 异常时无限死等。
 * TXE 表示数据寄存器可写，TC 表示最后一位已真正发完；复位前要等待后者。 */
static void uart_text(const char *text)
{
    uint32_t remaining;
    while (*text != '\0') {
        remaining = 100000U;
        while (!(USART2->SR & USART_SR_TXE)) {
            if (--remaining == 0U) return;
        }
        USART2->DR = (uint8_t)*text++;
    }
    remaining = 100000U;
    while (!(USART2->SR & USART_SR_TC)) {
        if (--remaining == 0U) return;
    }
}

/* 进入应用前的收尾，而不是直接调用应用的 main。
 * 向量表第 0 项是初始栈顶，第 1 项是带 Thumb 标志的 Reset_Handler 地址。
 * 调用者已通过完整镜像校验；本函数关闭串口/节拍和 NVIC，并把向量表改到应用分区。 */
static void jump_to_application(void)
{
    unsigned bank;
    const uint32_t *vectors = (const uint32_t *)OTA_APP_BASE;
    __disable_irq();
    USART2->CR1 = 0U;
    RCC->APB1RSTR |= RCC_APB1RSTR_USART2RST;
    RCC->APB1RSTR &= ~RCC_APB1RSTR_USART2RST;
    RCC->APB1ENR &= ~RCC_APB1ENR_USART2EN;
    /* Return PA2/PA3 to reset floating-input state; retain motor-safe GPIOC. */
    GPIOA->CRL = (GPIOA->CRL & ~0x0000FF00U) | 0x00004400U;
    SysTick->CTRL = 0U;
    SysTick->LOAD = 0U;
    SysTick->VAL = 0U;
/* 清除外部中断使能和挂起位，防止应用刚启动就收到 Bootloader 遗留的中断。
 * 随后还会清 SysTick/PendSV 挂起标志；电机 GPIO 保持安全电平。 */
    for (bank = 0; bank < 2U; ++bank) {
        NVIC->ICER[bank] = 0xFFFFFFFFU;
        NVIC->ICPR[bank] = 0xFFFFFFFFU;
    }
    SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk;
    SCB->VTOR = OTA_APP_BASE;
    __DSB();
    __ISB();
    /* Assembly tail: never use a C stack after replacing MSP. */
    Boot_Jump(vectors[0], vectors[1]);
    for (;;) { motors_safe(); }
}

/* 整体步骤：读取一次性升级标记 → 校验容量/日志 → 必要时续装 → 轮询命令 → 校验后启动应用。
 * elapsed 是轮询 SysTick COUNTFLAG 累积的计数，不是 RTOS 时钟；长时间 Flash 操作会影响计时精度。 */
int main(void)
{
    uint32_t elapsed=0,last_activity=0,last_beacon=0,status,previous_state;
    unsigned used=0;
    int collecting=0,forced,can_boot;
    uint8_t ch;
    const char *response;
    char tail[8];
    uart_init();
    RCC->APB1ENR|=RCC_APB1ENR_PWREN|RCC_APB1ENR_BKPEN;
    PWR->CR|=PWR_CR_DBP;
/* 应用请求升级时会写入两个备份寄存器。它们能跨系统复位保留，但不能假设跨彻底断电保留。
 * 读取后立即清除，让升级意图只消费一次，避免以后每次启动都误走强制等待窗口。 */
    forced=BKP->DR1==0x4F54U && BKP->DR2==0xB0ABU;
    BKP->DR1=0; BKP->DR2=0;
    uart_text("BOOT,2,START\r\n");
/* 读取芯片出厂 Flash 容量，单位 KiB。当前布局仅允许 256KiB 的 STM32F103RCT6。
 * 容量不符就停止，不在错误型号上擦写分区。 */
    if (*(const volatile uint16_t *)0x1FFFF7E0U != 256U) {
        uart_text("BOOT,2,FLASH_SIZE_ERROR\r\n");
        for(;;) { motors_safe(); }
    }
    OtaUpdate_Init(&update);
/* 上次已经开始覆盖应用时，旧应用可能只剩一半，不能直接跳转。
 * 先重新验证暂存镜像，再从第 0 页重装；失败则留在 Bootloader 等待恢复。 */
    if(update.valid && update.state==OTA_INSTALLING) {
        uart_text("BOOT,2,RESUMING_INSTALL\r\n");
        if(!OtaUpdate_Install(&update)) uart_text("BOOT,2,INSTALL_FAILED\r\n");
    }
/* 缓存完整应用校验结果，避免每收到一个 DATA 包都重新对整个应用计算 CRC。
 * 命令改变持久化状态后再更新；INSTALLING 状态始终不允许启动应用。 */
    can_boot=OtaUpdate_CanBoot(&update);
    if(!update.valid) uart_text("BOOT,3,INVALID_JOURNAL\r\n");
    else if(!can_boot && update.state!=OTA_INSTALLING) uart_text("BOOT,3,INVALID_APP\r\n");
    OtaProtocol_Init(&protocol,&update);
/* 8MHz 下每 8000 个周期置位一次 COUNTFLAG，约为 1ms；未置 TICKINT，不触发中断。 */
    SysTick->LOAD=7999U; SysTick->VAL=0U;
    SysTick->CTRL=SysTick_CTRL_CLKSOURCE_Msk|SysTick_CTRL_ENABLE_Msk;
    for (;;) {
        if(SysTick->CTRL & SysTick_CTRL_COUNTFLAG_Msk) ++elapsed;
        status=USART2->SR;
/* 出现溢出、帧错、噪声或校验错误时读 DR 清标志，并丢弃当前半帧，等待下一次 @ 重新同步。 */
        if(status&(USART_SR_ORE|USART_SR_FE|USART_SR_NE|USART_SR_PE)) {
            (void)USART2->DR; collecting=0; used=0;
        } else if(status&USART_SR_RXNE) {
            ch=(uint8_t)USART2->DR;
            if(ch=='@') { collecting=1; used=0; }
            else if(collecting && ch=='\n') {
                collecting=0;
/* 外层先验证 @载荷*CRC16\n 的完整性；只有通过 CRC 的载荷才交给 OTA 命令解析器。
 * 回复同样加 CRC16；reset_requested 只在回复发送完成后执行系统复位。 */
                if(link_decode(receive_line,used)==1) {
                    previous_state=update.state;
                    response=OtaProtocol_Process(&protocol,receive_line);
                    if(response) {
                        if(update.state!=previous_state) can_boot=OtaUpdate_CanBoot(&update);
                        last_activity=elapsed;
                        uart_text("@"); uart_text(response);
                        snprintf(tail,sizeof(tail),"*%04X\n",(unsigned)link_crc16(response,strlen(response)));
                        uart_text(tail);
                        if(protocol.reset_requested) NVIC_SystemReset();
                    }
                }
                used=0;
            } else if(collecting) {
                if(used+1U>=sizeof(receive_line)) { collecting=0; used=0; }
                else receive_line[used++]=(char)ch;
            }
        }
        /* Only a blocked Bootloader advertises recovery. A normal boot must
         * never be mistaken for a stationary recovery board by ESP32. */
/* 只有无法启动应用时才周期广播恢复状态，防止 ESP32 把正常开机误判为升级故障。
 * BOOT_STATE,3,状态,当前版本：状态 0 表示日志无效；普通日志文字不等于受校验的恢复帧。 */
        if(!can_boot && (uint32_t)(elapsed-last_beacon)>=1000U) {
            char beacon[80];
            last_beacon=elapsed;
            snprintf(beacon,sizeof(beacon),"BOOT_STATE,3,%lu,%lu",(unsigned long)(update.valid?update.state:0U),
                     (unsigned long)(update.valid?update.current.version:0U));
            uart_text("@"); uart_text(beacon);
            snprintf(tail,sizeof(tail),"*%04X\n",(unsigned)link_crc16(beacon,strlen(beacon)));uart_text(tail);
        }
/* 普通启动只等待约 500ms；有强制标记或升级会话时，等待约 30 秒无有效活动。
 * 超时只是尝试启动：如果镜像或日志不安全，仍然留在恢复循环，绝不会为了超时而强行跳转。 */
        if((uint32_t)(elapsed-last_activity)>=((protocol.session||forced)?30000U:500U)) {
            if(can_boot) {
                uart_text("BOOT,2,APP_VALID\r\n"); jump_to_application();
            }
            last_activity=elapsed;
        }
    }
}
