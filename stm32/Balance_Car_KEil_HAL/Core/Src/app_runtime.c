#include "app_runtime.h"
#include "FreeRTOS.h"
#include "queue.h"
#include "task.h"
#include "AllHeader.h"
#include "ChassisLink/link_frame.h"
#include <string.h>
#include <stdio.h>

typedef struct { uint32_t epoch, received; unsigned int bluetooth, safety; char text[128]; } Command;
typedef struct { unsigned int bluetooth; char text[201]; } Transmission;
static QueueHandle_t commands, urgent, replies, telemetry;
static volatile uint32_t ble_epoch, esp_epoch;
static volatile unsigned int ready;
static volatile unsigned int stop_pending;
static volatile uint32_t command_full, command_stale, reply_full, telemetry_dropped, tx_timeout;

void AppRuntime_Init(void)
{
    commands = xQueueCreate(8, sizeof(Command));
    urgent = xQueueCreate(4, sizeof(Command));
    replies = xQueueCreate(16, sizeof(Transmission));
    telemetry = xQueueCreate(16, sizeof(Transmission));
    if (!commands || !urgent || !replies || !telemetry) Error_Handler();
}
void AppRuntime_SetReady(void) { ready = 1; }
int AppRuntime_IsReady(void) { return ready != 0; }

void AppRuntime_InvalidateBluetooth(void)
{
    /* Called by the control/watchdog IRQ too. Never use an RTOS API here. */
    uint32_t mask = __get_PRIMASK();
    __disable_irq();
    ++ble_epoch;
    __set_PRIMASK(mask);
}

int AppRuntime_Send(unsigned int bluetooth, const char *text, unsigned int low)
{
    Transmission item;
    size_t length;
    if (!text || !replies) return 0;
    length = strlen(text);
    if (!length || length >= sizeof(item.text)) return 0;
    item.bluetooth = bluetooth;
    memcpy(item.text, text, length + 1);
    /* Replies can wait at most 5 ms for the transmitter. Telemetry never holds
     * up action processing. A failed reply is observable and revokes movement. */
    if (xQueueSend(low ? telemetry : replies, &item, low ? 0 : pdMS_TO_TICKS(5)) == pdPASS) return 1;
    if (low) ++telemetry_dropped;
    else { ++reply_full; stop_pending = 1; }
    return 0;
}

uint32_t AppRuntime_BluetoothEpoch(void) { return ble_epoch; }

static void post_command(unsigned int bluetooth, const char *text, uint32_t captured_epoch)
{
    Command item;
    int safety;
    if (!text || strlen(text) >= sizeof(item.text)) return;
    if (bluetooth && captured_epoch != ble_epoch) { ++command_stale; return; }
    safety = bluetooth
        ? (!strcmp(text, "$0,0,0,0,0,0,0,0,0,0#") || !strncmp(text, "$BC,0,", 6))
        : (!strcmp(text, "BT_REVOKE") || !strncmp(text, "BT,0,", 5));
    if (safety) {
        if (bluetooth) AppRuntime_InvalidateBluetooth();
        else ++esp_epoch;
    }
    item.bluetooth = bluetooth;
    item.safety = safety;
    item.epoch = bluetooth ? (safety ? ble_epoch : captured_epoch) : esp_epoch;
    item.received = HAL_GetTick();
    strcpy(item.text, text);
    if (xQueueSend(safety ? urgent : commands, &item, 0) != pdPASS) {
        ++command_full;
        /* Queue exhaustion cannot leave a previously held direction running. */
        stop_pending = 1;
        if (!bluetooth) ChassisLink_RejectCommand(text, "BUSY");
    }
}

void AppRuntime_PostCommand(unsigned int bluetooth, const char *text)
{
    post_command(bluetooth, text, ble_epoch);
}
void AppRuntime_PostBluetooth(const char *text, uint32_t epoch)
{
    post_command(1, text, epoch);
}

void AppRuntime_ActionPoll(void)
{
    Command item;
    unsigned int count = 0;
    {
        uint32_t mask = __get_PRIMASK();
        unsigned int pending;
        __disable_irq();
        pending = stop_pending;
        stop_pending = 0;
        if (pending) ++esp_epoch;
        __set_PRIMASK(mask);
        if (pending) ChassisLink_ExecuteCommand("BT_REVOKE");
    }
    /* Bounded work per tick leaves watchdogs and posture completion runnable. */
    while (count++ < 4 && (xQueueReceive(urgent, &item, 0) == pdPASS ||
                           xQueueReceive(commands, &item, 0) == pdPASS)) {
        if (item.epoch != (item.bluetooth ? ble_epoch : esp_epoch) ||
            (!item.safety && (uint32_t)(HAL_GetTick() - item.received) > 500U)) {
            ++command_stale;
            if (!item.bluetooth) ChassisLink_RejectCommand(item.text, "CANCELLED");
            continue;
        }
        if (item.bluetooth) Bluetooth_ExecutePacket(item.text);
        else ChassisLink_ExecuteCommand(item.text);
    }
}

void AppRuntime_TransmitPoll(void)
{
    Transmission item;
    char frame[208];
    const char *data;
    size_t length, i;
    uint32_t started;
    USART_TypeDef *uart;
    /* Do not wait on the low queue: a newly queued ACK must win next frame. */
    if (xQueueReceive(replies, &item, 0) != pdPASS &&
        xQueueReceive(telemetry, &item, 0) != pdPASS) return;
    data = item.text;
    uart = item.bluetooth ? UART5 : USART2;
    if (!item.bluetooth) {
        int written;
        length = strcspn(data, "\r\n");
        written = snprintf(frame, sizeof(frame), "@%.*s*%04X\n", (int)length,
                           data, (unsigned int)link_crc16(data, length));
        if (written <= 0 || (size_t)written >= sizeof(frame)) return;
        data = frame;
    }
    length = strlen(data);
    started = HAL_GetTick();
    for (i = 0; i < length; ++i) {
        while (!LL_USART_IsActiveFlag_TXE(uart)) {
            if ((uint32_t)(HAL_GetTick() - started) > 300U) {
                ++tx_timeout;
                stop_pending = 1;
                return;
            }
            taskYIELD();
        }
        LL_USART_TransmitData8(uart, (uint8_t)data[i]);
    }
}

void AppRuntime_Report(void)
{
    char report[128];
    snprintf(report, sizeof(report), "DIAG,QUEUE,1,%lu,%lu,%lu,%lu,%lu\n",
             (unsigned long)command_full, (unsigned long)command_stale,
             (unsigned long)reply_full, (unsigned long)telemetry_dropped,
             (unsigned long)tx_timeout);
    AppRuntime_Send(0, report, 1);
}
