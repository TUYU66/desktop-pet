#ifndef APP_RUNTIME_H
#define APP_RUNTIME_H
#include <stdint.h>
/* Only the RX rings and epoch invalidation may be called from interrupts.
 * Queue operations below are task-only; UART IRQs remain above max syscall. */
void AppRuntime_Init(void);
void AppRuntime_SetReady(void);
int AppRuntime_IsReady(void);
void AppRuntime_PostCommand(unsigned int bluetooth, const char *text);
void AppRuntime_InvalidateBluetooth(void);
uint32_t AppRuntime_BluetoothEpoch(void);
void AppRuntime_PostBluetooth(const char *text, uint32_t epoch);
int AppRuntime_Send(unsigned int bluetooth, const char *text, unsigned int telemetry);
void AppRuntime_ActionPoll(void);
void AppRuntime_TransmitPoll(void);
void AppRuntime_Report(void);
#endif
