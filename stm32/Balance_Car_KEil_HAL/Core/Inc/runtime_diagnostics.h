#ifndef RUNTIME_DIAGNOSTICS_H
#define RUNTIME_DIAGNOSTICS_H

#include <stdint.h>

/* Start/Poll: AppTask only. RecordControl: IMU IRQ only, no RTOS or I/O. */
void RuntimeDiagnostics_Start(void);
void RuntimeDiagnostics_Poll(void);
void RuntimeDiagnostics_RecordControl(uint32_t started, uint32_t finished);

#endif
