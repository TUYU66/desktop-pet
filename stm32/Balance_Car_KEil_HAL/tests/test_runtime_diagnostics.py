"""Run production resource/timing statistics with simulated kernel and DWT ports."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_units_windows_wraparound_throttling_and_irq_boundaries(self):
        compiler = os.environ.get('CC') or shutil.which('gcc')
        if not compiler:
            self.fail('Set CC to a host C compiler')
        headers = {
            'FreeRTOS.h': '#include <stdint.h>\n#include <stddef.h>\ntypedef uint32_t StackType_t;\n',
            'task.h': 'unsigned uxTaskGetStackHighWaterMark(void *);\nvoid *xTaskGetIdleTaskHandle(void);\nsize_t xPortGetFreeHeapSize(void);\nsize_t xPortGetMinimumEverFreeHeapSize(void);\n',
            'timers.h': 'void *xTimerGetTimerDaemonTaskHandle(void);\n',
            'main.h': '#include <stdint.h>\nextern uint32_t SystemCoreClock;\nuint32_t HAL_GetTick(void);\nuint32_t __get_PRIMASK(void);\nvoid __disable_irq(void);\nvoid __set_PRIMASK(uint32_t);\n',
            'ChassisLink/chassis_link.h': 'void ChassisLink_SendDiagnostic(const char *);\n',
        }
        program = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
void RuntimeDiagnostics_ReportTasks(void) {}
uint32_t SystemCoreClock = 72000000U;
static uint32_t tick, irq_mask;
static int in_irq, count;
static char reports[16][128];
static void task_boundary(void) { assert(!in_irq && !irq_mask); }
uint32_t HAL_GetTick(void) { task_boundary(); return tick; }
uint32_t __get_PRIMASK(void) { return irq_mask; }
void __disable_irq(void) { irq_mask = 1; }
void __set_PRIMASK(uint32_t mask) { irq_mask = mask; }
unsigned uxTaskGetStackHighWaterMark(void *task) {
    task_boundary(); return task == (void *)1 ? 80 : task == (void *)2 ? 120 : 100;
}
void *xTaskGetIdleTaskHandle(void) { task_boundary(); return (void *)1; }
void *xTimerGetTimerDaemonTaskHandle(void) { task_boundary(); return (void *)2; }
size_t xPortGetFreeHeapSize(void) { task_boundary(); return 9000; }
size_t xPortGetMinimumEverFreeHeapSize(void) { task_boundary(); return 8500; }
void ChassisLink_SendDiagnostic(const char *s) {
    task_boundary(); assert(count < 16 && strlen(s) < 128);
    strcpy(reports[count++], s);
}
'''
        program += '#include "' + (ROOT / 'Core/Src/runtime_diagnostics.c').as_posix() + '"\n'
        program += r'''
static void sample(uint32_t started, uint32_t finished) {
    in_irq = 1; RuntimeDiagnostics_RecordControl(started, finished); in_irq = 0;
}
static void expect_pair(unsigned samples, unsigned min, unsigned max, unsigned elapsed, unsigned overruns) {
    unsigned long timestamp, got_samples, got_min, got_max, got_elapsed, got_overruns;
    int first = count;
    RuntimeDiagnostics_Poll(); assert(count == first + 1);
    assert(strstr(reports[first], ",400,320,480,9000,8500\n") != NULL);
    RuntimeDiagnostics_Poll(); assert(count == first + 2);
    assert(sscanf(reports[first + 1], "DIAG,IRQ,1,%lu,%lu,%lu,%lu,%lu,%lu",
        &timestamp, &got_samples, &got_min, &got_max, &got_elapsed, &got_overruns) == 6);
    assert(timestamp == tick && got_samples == samples && got_min == min && got_max == max);
    assert(got_elapsed == elapsed && got_overruns == overruns);
    RuntimeDiagnostics_Poll(); assert(count == first + 2);
}
int main(void) {
    unsigned i;
    uint32_t start;
    /* Dormant before AppTask starts; IRQ observations cannot call kernel/I/O. */
    sample(0, 72000); RuntimeDiagnostics_Poll(); assert(count == 0);
    tick = 0; RuntimeDiagnostics_Start();
    for (i = 0; i < 1000; ++i) sample(1000000U + i * 360000U, 1072000U + i * 360000U);
    tick = 4999; RuntimeDiagnostics_Poll(); assert(count == 0);
    tick = 5000; expect_pair(1000, 5000, 5000, 1000, 0);
    tick = 10000; expect_pair(0, 0, 0, 0, 0);
    /* First observation has no invented interval. */
    RuntimeDiagnostics_Start(); sample(100, 72100);
    tick = 15000; expect_pair(1, 0, 0, 1000, 0);
    /* DWT wrap and an execution longer than the 5 ms control budget. */
    RuntimeDiagnostics_Start(); start = UINT32_MAX - 72000U;
    sample(start, start + 144000U);
    sample(start + 360000U, start + 360000U + 432000U);
    tick = 20000; expect_pair(2, 5000, 5000, 6000, 1);
    /* HAL millisecond wrap must not suppress reports or emit one early. */
    tick = UINT32_MAX - 15U; RuntimeDiagnostics_Start();
    i = count; tick += 4999U; RuntimeDiagnostics_Poll(); assert(count == (int)i);
    ++tick; expect_pair(0, 0, 0, 0, 0);
    puts("resource units, timing windows, IRQ boundaries and wraparound passed");
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='stm32-diagnostics-') as folder:
            work = Path(folder)
            for name, content in headers.items():
                file = work / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content)
            file = work / 'test.c'
            executable = work / 'test.exe'
            file.write_text(program)
            subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-I', str(work),
                            '-I', str(ROOT / 'Core/Inc'), str(file), '-o', str(executable)],
                           check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
