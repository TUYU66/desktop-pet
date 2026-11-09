"""Exercise the real RTOS boot/task/IRQ glue with hardware boundaries stubbed."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def function(path, signature):
    source = (ROOT / path).read_bytes().decode("latin1")
    match = re.search(re.escape(signature) + r"\s*\{", source)
    if not match:
        raise AssertionError(f"Missing definition: {signature}")
    depth = 1
    end = match.end()
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


class FreeRTOSRuntimeTests(unittest.TestCase):
    def test_byte_receivers_preempt_blocking_imu_and_match_cubemx(self):
        usart = (ROOT / "Core/Src/usart.c").read_text()
        gpio = (ROOT / "Core/Src/gpio.c").read_text()
        config = (ROOT / "Core/Inc/FreeRTOSConfig.h").read_text()
        ioc = (ROOT / "Balance_Car_KEil_HAL.ioc").read_text()
        imu = int(re.search(r"HAL_NVIC_SetPriority\(EXTI15_10_IRQn,\s*(\d+)", gpio)[1])
        limit = int(re.search(r"#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY\s+(\d+)", config)[1])
        for uart in ("USART2", "UART5"):
            priority = int(re.search(r"NVIC_SetPriority\(" + uart + r"_IRQn, NVIC_EncodePriority\(NVIC_GetPriorityGrouping\(\),(\d+)", usart)[1])
            self.assertLess(priority, imu, "Without RX DMA, software-I2C must not block the UART data register")
            self.assertLess(priority, limit)
            self.assertIn(f"NVIC.{uart}_IRQn=true\\:{priority}\\:0", ioc)

    def test_boot_task_timer_receive_and_failure_paths(self):
        compiler = os.environ.get("CC") or shutil.which("gcc")
        if not compiler:
            self.fail("Set CC to a host C compiler")
        glue = "\n".join([
            function("Core/Src/main.c", "int main(void)").replace("int main(void)", "int firmware_main(void)", 1),
            function("Core/Src/main.c", "void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)"),
            function("Core/Src/main.c", "void Error_Handler(void)"),
            function("Core/Src/stm32f1xx_it.c", "void USART2_IRQHandler(void)"),
            function("Core/Src/freertos.c", "void MX_FREERTOS_Init(void)"),
            function("Core/Src/freertos.c", "void StartAppTask(void *argument)"),
            function("Core/Src/freertos.c", "void vApplicationStackOverflowHook(TaskHandle_t task, char *name)"),
            function("Core/Src/freertos.c", "void vApplicationMallocFailedHook(void)"),
        ])
        harness = r'''
#include <assert.h>
#include <setjmp.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef void *TaskHandle_t;
typedef void *osThreadId_t;
typedef struct { void *Instance; } TIM_HandleTypeDef;
#define TIM6 ((void *)6)
#define TIM7 ((void *)7)
static jmp_buf escape;
static char events[64];
static unsigned used, loops, ticks, checks, tim6_calls, rx_calls;
static int scheduler_starts, allocation_succeeds;
static volatile unsigned char Stop_Flag;
static int Motor_Left, Motor_Right;
static osThreadId_t ActionTaskHandle;
static const int ActionTask_attributes = 1;
static osThreadId_t ReceiveTaskHandle, MonitorTaskHandle, TransmitTaskHandle;
static const int ReceiveTask_attributes=2, MonitorTask_attributes=3, TransmitTask_attributes=4;
static void StartReceiveTask(void *p) {(void)p;}
static void StartMonitorTask(void *p) {(void)p;}
static void StartTransmitTask(void *p) {(void)p;}
static void AppRuntime_Init(void) {}
static void AppRuntime_SetReady(void) {}
void Error_Handler(void);
void MX_FREERTOS_Init(void);
void StartAppTask(void *argument);
static void event(char c) { events[used++] = c; events[used] = 0; }
static void HAL_Init(void) {}
static void SystemClock_Config(void) {}
static void MX_GPIO_Init(void) {}
static void MX_TIM3_Init(void) {}
static void MX_TIM4_Init(void) {}
static void MX_TIM6_Init(void) {}
static void MX_TIM8_Init(void) {}
static void MX_USART1_UART_Init(void) {}
static void MX_ADC1_Init(void) {}
static void MX_UART5_Init(void) {}
static void MX_USART2_UART_Init(void) {}
static void bsp_init(void) { event('B'); }
static int osKernelInitialize(void) { event('K'); return 0; }
static osThreadId_t osThreadNew(void (*entry)(void *), void *arg, const void *attr) {
    assert(arg == NULL);
    assert((entry == StartAppTask && attr == &ActionTask_attributes) ||
           (entry == StartReceiveTask && attr == &ReceiveTask_attributes) ||
           (entry == StartMonitorTask && attr == &MonitorTask_attributes) ||
           (entry == StartTransmitTask && attr == &TransmitTask_attributes));
    event('T'); return allocation_succeeds ? (void *)1 : NULL;
}
static int osKernelStart(void) {
    event('S'); if (scheduler_starts) longjmp(escape, 1); return -1;
}
static void bsp_services_init(void) { event('C'); }
static void MPU6050_EXTI_Init(void) { event('I'); }
static void OLED_Draw_Line(const char *s, int line, int reverse, int clear) {
    (void)s; (void)line; (void)reverse; (void)clear; event('O');
}
#define false 0
#define true 1
static void app_user(void) { event('A'); }
static unsigned runtime_polls;
static void RuntimeDiagnostics_Start(void) {}
static void RuntimeDiagnostics_Poll(void) { ++runtime_polls; }
static void osDelay(unsigned ticks_to_wait) {
    assert(ticks_to_wait == 2); event('D'); if (++loops == 2) longjmp(escape, 3);
}
static void __disable_irq(void) { event('X'); }
static void Set_Pwm(int left, int right) {
    assert(left == 0 && right == 0 && Stop_Flag == 1);
    assert(Motor_Left == 0 && Motor_Right == 0);
    event('P'); longjmp(escape, 2);
}
static void BSP_TimerPeriodElapsed(TIM_HandleTypeDef *timer) {
    if (timer->Instance == TIM6) ++tim6_calls;
}
static void HAL_IncTick(void) { ++ticks; }
static void Chassis_CalibrationWatchdog(void) { assert(ticks > checks); ++checks; }
static void ChassisLink_OnRxInterrupt(void) { ++rx_calls; }
'''
        checks = r'''
static void reset(void) {
    used = loops = 0; events[0] = 0;
    Stop_Flag = 1; Motor_Left = Motor_Right = 0;
}
int main(void) {
    int result;
    TIM_HandleTypeDef timer;
    reset(); scheduler_starts = allocation_succeeds = 1;
    result = setjmp(escape);
    if (!result) firmware_main();
    assert(result == 1 && strcmp(events, "BOKTTTTS") == 0);
    /* Both receive polling and the yield remain active before any STAND. */
    reset(); result = setjmp(escape);
    if (!result) StartAppTask(NULL);
    assert(result == 3 && Stop_Flag == 1 && strcmp(events, "OCIOADAD") == 0);
    assert(runtime_polls == 2);
    reset(); scheduler_starts = 0; result = setjmp(escape);
    if (!result) firmware_main();
    assert(result == 2 && strcmp(events, "BOKTTTTSXP") == 0);
    reset(); allocation_succeeds = 0; result = setjmp(escape);
    if (!result) firmware_main();
    assert(result == 2 && strcmp(events, "BOKTXP") == 0);
    timer.Instance = TIM6; HAL_TIM_PeriodElapsedCallback(&timer);
    assert(tim6_calls == 1 && ticks == 0 && checks == 0);
    timer.Instance = TIM7; HAL_TIM_PeriodElapsedCallback(&timer);
    assert(tim6_calls == 1 && ticks == 1 && checks == 1);
    USART2_IRQHandler(); assert(rx_calls == 1);
    reset(); Motor_Left = 500; Motor_Right = -500; result = setjmp(escape);
    if (!result) vApplicationStackOverflowHook(NULL, NULL);
    assert(result == 2 && strcmp(events, "XP") == 0);
    reset(); result = setjmp(escape);
    if (!result) vApplicationMallocFailedHook();
    assert(result == 2 && strcmp(events, "XP") == 0);
    puts("RTOS boot/task/IRQ/failure regressions passed");
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="stm32-rtos-") as folder:
            source = Path(folder) / "runtime.c"
            executable = Path(folder) / "runtime.exe"
            source.write_text(harness + glue + checks, encoding="latin1")
            subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", str(source), "-o", str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)

    def test_uart5_startup_and_error_recovery(self):
        compiler = os.environ.get("CC") or shutil.which("gcc")
        if not compiler:
            self.fail("Set CC to a host C compiler")
        source = r'''
#include <assert.h>
#include <stdint.h>
typedef struct { volatile uint32_t SR; uint8_t DR; int enabled, rxit; } Uart;
static Uart uart;
#define UART5 (&uart)
#define USART_SR_RXNE (1U << 5)
#define USART_SR_ORE (1U << 3)
#define USART_SR_NE (1U << 2)
#define USART_SR_FE (1U << 1)
#define USART_SR_PE (1U << 0)
#define BLE_RX_CAPACITY 256U
static uint8_t rx_bytes[BLE_RX_CAPACITY];
static volatile uint16_t rx_head, rx_tail;
static volatile uint8_t rx_invalid;
static unsigned init_count, read_count, parsed, discarded, pid_ready;
static void AppRuntime_InvalidateBluetooth(void) { ++discarded; }
static int LL_USART_IsEnabled(Uart *u) { return u->enabled; }
static int LL_USART_IsEnabledIT_RXNE(Uart *u) { return u->rxit; }
static void MX_UART5_Init(void) { assert(!uart.enabled); uart.enabled = 1; ++init_count; }
static void LL_USART_DisableIT_RXNE(Uart *u) { u->rxit = 0; }
static uint8_t LL_USART_ReceiveData8(Uart *u) { ++read_count; u->SR = 0; return u->DR; }
static void Init_PID(void) { pid_ready = 1; }
static void LL_USART_EnableIT_RXNE(Uart *u) {
    assert(pid_ready && u->SR == 0); u->rxit = 1;
}
static void deal_bluetooth(uint8_t b) { assert(b == 0x42); ++parsed; }
static void Bluetooth_DiscardInput(void) { ++discarded; }
'''
        source += function("BSP/Bluetooth/bsp_bluetooth.c", "void bluetooth_init(void)")
        source += function("BSP/Bluetooth/bsp_bluetooth.c", "void UART5_RX_deal(void)")
        source += r'''
int main(void) {
    unsigned i, reads;
    uint32_t errors[] = {USART_SR_ORE, USART_SR_FE, USART_SR_NE, USART_SR_PE};
    /* CubeMX already enabled the UART; discard accumulated startup bytes. */
    uart.enabled = 1; uart.SR = USART_SR_ORE | USART_SR_RXNE; uart.DR = 0x42;
    bluetooth_init();
    assert(init_count == 0 && read_count == 1 && uart.rxit && parsed == 0);
    UART5_RX_deal(); assert(read_count == 1 && parsed == 0);
    uart.SR = USART_SR_RXNE; UART5_RX_deal(); assert(parsed == 0 && rx_head == 1 && rx_bytes[0] == 0x42);
    for (i = 0; i < 4; ++i) {
        uart.SR = USART_SR_RXNE | errors[i]; UART5_RX_deal();
        assert(uart.SR == 0 && discarded == i + 1 && parsed == 0 && rx_invalid);
    }
    uart.SR = USART_SR_ORE; UART5_RX_deal(); assert(uart.SR == 0 && discarded == 5);
    reads = read_count; uart.rxit = 0; uart.SR = USART_SR_RXNE;
    UART5_RX_deal(); assert(read_count == reads);
    /* The BSP also remains usable when no generated main initialized UART5. */
    uart.enabled = 0; pid_ready = 0; bluetooth_init(); assert(init_count == 1 && uart.rxit);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="stm32-uart-") as folder:
            file = Path(folder) / "runtime.c"
            executable = Path(folder) / "runtime.exe"
            file.write_text(source, encoding="latin1")
            subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", str(file), "-o", str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
