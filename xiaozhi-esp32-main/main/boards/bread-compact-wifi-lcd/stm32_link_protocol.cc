#include "stm32_link.h"
#include "config.h"
#include "application.h"
#include "link_frame.h"
#include "mcp_server.h"
#include "board.h"
#include "display.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cerrno>
#include <memory>
#include <string_view>
#include <esp_random.h>
#include <nvs.h>
#include <driver/uart.h>
#include <esp_err.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "stm32_link_internal.h"

namespace stm32_link_detail {
// Caller holds uart_mutex_. Payloads are short, generated ASCII commands.
int WriteFrame(const char* payload) {
    char frame[144];
    const size_t length = strlen(payload);
    if (length == 0 || length > sizeof(frame) - 8) return -1;
    const int size = snprintf(frame, sizeof(frame), "@%s*%04X\n", payload,
                              static_cast<unsigned int>(link_crc16(payload, length)));
    if (size <= 0 || size >= static_cast<int>(sizeof(frame))) return -1;
    const int written = uart_write_bytes(kUart, frame, size);
    return written == size ? written : -1;
}


// Coarse 3S Li-ion voltage estimate. Motor load and cell chemistry affect it;
// both the UI and spoken reply explicitly identify it as an estimate.
int EstimateBatteryPercent(int millivolts) {
    if (millivolts <= 9600) return 0;
    if (millivolts >= 12600) return 100;
    return (millivolts - 9600) * 100 / 3000;
}

// Exact CSV arity, no partial conversions/trailing garbage. int64_t also
// accepts uint32_t STM32 tick/counter values after long uptimes.
bool ParseFields(const char* text, int64_t* fields, size_t count) {
    for (size_t i = 0; i < count; ++i) {
        if (!(*text >= '0' && *text <= '9') && *text != '-') return false;
        char* end = nullptr;
        errno = 0;
        fields[i] = strtoll(text, &end, 10);
        if (end == text || errno == ERANGE || fields[i] < INT32_MIN || fields[i] > UINT32_MAX) return false;
        if (i + 1 == count) return *end == '\0';
        if (*end != ',') return false;
        text = end + 1;
    }
    return false;
}

bool TuningValuesValid(const int64_t* p) {
    return p[0]>=-10000 && p[0]<=10000 && p[1]>=100000 && p[1]<=2000000 &&
        p[2]>=0 && p[2]<=20000 && p[3]>=0 && p[3]<=1200000 &&
        p[4]>=0 && p[4]<=10000 && p[5]>=0 && p[5]<=6000;
}
std::string TuningParamsJson(const int64_t* p) {
    static const char* keys[] = {"midAngle","balanceKp","balanceKd","velocityKp","velocityKi","turnKd"};
    std::string json="{";
    for (int i=0;i<6;++i) {
        if (i) json+=",";
        json+=std::string("\"")+keys[i]+"\":"+std::to_string(p[i]/(i==0?1000.0:100.0));
    }
    return json+"}";
}
std::string ReadSavedTuning() {
    nvs_handle_t handle;
    if (nvs_open("chassis_tune",NVS_READONLY,&handle)!=ESP_OK) return "";
    char text[112]{};
    size_t size=sizeof(text);
    const esp_err_t result=nvs_get_str(handle,"values_v1",text,&size);
    nvs_close(handle);
    int64_t values[6]{};
    return result==ESP_OK && ParseFields(text,values,6) && TuningValuesValid(values)?text:"";
}


}
