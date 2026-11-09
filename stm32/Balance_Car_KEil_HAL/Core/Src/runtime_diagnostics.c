#include "runtime_diagnostics.h"
#include "FreeRTOS.h"
#include "task.h"
#include "timers.h"
#include "main.h"
#include "ChassisLink/chassis_link.h"
#include <stdio.h>
extern void RuntimeDiagnostics_ReportTasks(void);

#define REPORT_INTERVAL_MS 5000U
#define CONTROL_RATE_HZ 200U /* Existing MPU6050 sample rate; observation only. */

typedef struct {
    uint32_t samples;
    uint32_t period_min;
    uint32_t period_max;
    uint32_t execution_max;
    uint32_t overruns;
} ControlWindow;

static volatile ControlWindow control;
static volatile uint32_t previous_start;
static volatile uint8_t have_previous, enabled;
static uint32_t last_report, cycles_per_us, control_budget_cycles;
static uint8_t pending_irq;
static char irq_report[128];

void RuntimeDiagnostics_Start(void)
{
    uint32_t mask = __get_PRIMASK();
    __disable_irq();
    enabled = 0;
    control.samples = control.period_max = control.execution_max = control.overruns = 0;
    control.period_min = UINT32_MAX;
    have_previous = 0;
    previous_start = 0;
    cycles_per_us = SystemCoreClock / 1000000U;
    if (!cycles_per_us) cycles_per_us = 1U;
    control_budget_cycles = SystemCoreClock / CONTROL_RATE_HZ;
    enabled = 1;
    __set_PRIMASK(mask);
    last_report = HAL_GetTick();
    pending_irq = 0;
}

void RuntimeDiagnostics_RecordControl(uint32_t started, uint32_t finished)
{
    uint32_t elapsed;
    if (!enabled) return;
    if (have_previous) {
        /* Unsigned subtraction handles DWT's ~59 s wrap at 72 MHz. */
        uint32_t period = started - previous_start;
        if (period < control.period_min) control.period_min = period;
        if (period > control.period_max) control.period_max = period;
    }
    previous_start = started;
    have_previous = 1;
    elapsed = finished - started;
    if (elapsed > control.execution_max) control.execution_max = elapsed;
    if (elapsed >= control_budget_cycles) ++control.overruns;
    ++control.samples;
}

void RuntimeDiagnostics_Poll(void)
{
    uint32_t now, mask;
    ControlWindow snapshot;
    static char resources[128];
    unsigned long app_stack, idle_stack, timer_stack, free_heap, min_heap;
    if (!enabled) return;
    if (pending_irq) {
        pending_irq = 0;
        ChassisLink_SendDiagnostic(irq_report);
        return;
    }
    now = HAL_GetTick();
    if ((uint32_t)(now - last_report) < REPORT_INTERVAL_MS) return;
    last_report = now;
    /* IMU IRQ priority is above the RTOS syscall threshold: a task critical
     * section alone would NOT protect this snapshot. Only copy/reset counters
     * under PRIMASK; all scans, formatting and UART writes follow afterward. */
    mask = __get_PRIMASK();
    __disable_irq();
    snapshot.samples = control.samples;
    snapshot.period_min = control.period_min;
    snapshot.period_max = control.period_max;
    snapshot.execution_max = control.execution_max;
    snapshot.overruns = control.overruns;
    control.samples = control.period_max = control.execution_max = control.overruns = 0;
    control.period_min = UINT32_MAX;
    __set_PRIMASK(mask);

    app_stack = (unsigned long)uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t);
    idle_stack = (unsigned long)uxTaskGetStackHighWaterMark(xTaskGetIdleTaskHandle()) * sizeof(StackType_t);
    timer_stack = (unsigned long)uxTaskGetStackHighWaterMark(xTimerGetTimerDaemonTaskHandle()) * sizeof(StackType_t);
    free_heap = (unsigned long)xPortGetFreeHeapSize();
    min_heap = (unsigned long)xPortGetMinimumEverFreeHeapSize();
    snprintf(resources, sizeof(resources), "DIAG,RTOS,1,%lu,%lu,%lu,%lu,%lu,%lu\n",
             (unsigned long)now, app_stack, idle_stack, timer_stack, free_heap, min_heap);
    snprintf(irq_report, sizeof(irq_report), "DIAG,IRQ,1,%lu,%lu,%lu,%lu,%lu,%lu\n",
             (unsigned long)now, (unsigned long)snapshot.samples,
             (unsigned long)(snapshot.period_min == UINT32_MAX ? 0 : snapshot.period_min / cycles_per_us),
             (unsigned long)(snapshot.period_max / cycles_per_us),
             (unsigned long)(snapshot.execution_max / cycles_per_us),
             (unsigned long)snapshot.overruns);
    /* Copy diagnostics to the transport queues. Only TransmitTask writes UART. */
    ChassisLink_SendDiagnostic(resources);
    pending_irq = 1;
    RuntimeDiagnostics_ReportTasks();
}
