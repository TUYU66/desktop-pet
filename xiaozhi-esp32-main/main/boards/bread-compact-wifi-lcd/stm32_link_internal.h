#pragma once
// Shared implementation details of the UART bridge; not an application API.
#include <cstdint>
#include <cstddef>
#include <string>
#include <driver/uart.h>
namespace stm32_link_detail {
inline constexpr uart_port_t kUart = UART_NUM_1;
inline constexpr char kTag[] = "Stm32Link";
enum ReplyCode {
    kNoReply,
    kUnconfirmed,
    kAccepted,
    kNotReady,
    kLowBattery,
    kAngle,
    kNotStanding,
    kFault,
    kBusy,
    kBluetoothActive,
    kBluetoothCompleted,
    kBluetoothStandingCompleted,
    kBluetoothRestingCompleted,
    kBluetoothStandFailed,
    kBluetoothRestFailed,
    kCompleted,
    kPostureCompleted,
    kAborted,
    kSettleAfter,
    kNotStable,
    kUnknownError,
};
void InterruptBluetoothPrompt();
int WriteFrame(const char* payload);
int EstimateBatteryPercent(int millivolts);
bool ParseFields(const char* text, int64_t* fields, size_t count);
bool TuningValuesValid(const int64_t* values);
std::string TuningParamsJson(const int64_t* values);
std::string ReadSavedTuning();
}
