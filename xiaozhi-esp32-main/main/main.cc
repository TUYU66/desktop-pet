#include <esp_log.h>
#include <esp_err.h>
#include <esp_system.h>
#include <nvs.h>
#include <nvs_flash.h>
#include <driver/gpio.h>
#include <esp_event.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "application.h"

#define TAG "main"

static const char* ResetReasonName(esp_reset_reason_t reason) {
    switch (reason) {
        case ESP_RST_POWERON: return "power_on";
        case ESP_RST_EXT: return "external_reset";
        case ESP_RST_SW: return "software_restart";
        case ESP_RST_PANIC: return "panic";
        case ESP_RST_INT_WDT: return "interrupt_watchdog";
        case ESP_RST_TASK_WDT: return "task_watchdog";
        case ESP_RST_WDT: return "watchdog";
        case ESP_RST_DEEPSLEEP: return "deep_sleep";
        case ESP_RST_BROWNOUT: return "brownout";
        case ESP_RST_PWR_GLITCH: return "power_glitch";
        default: return "other";
    }
}

extern "C" void app_main(void)
{
    const auto reset_reason = esp_reset_reason();
    ESP_LOGW(TAG, "boot_reset reason=%d name=%s diagnostic_revision=2026101401",
             static_cast<int>(reset_reason), ResetReasonName(reset_reason));
    // ESP-IDF 的 C 入口。先初始化 NVS，因为 Wi-Fi 凭据、OTA 地址等运行配置都保存在这里。
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        // NVS 分区写满或格式版本不兼容时只能擦除后重建；正常启动不会进入这里。
        ESP_LOGW(TAG, "Erasing NVS flash to fix corruption");
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Application 是全局唯一实例：Initialize 组装硬件和回调，Run 进入永久事件循环。
    auto& app = Application::GetInstance();
    app.Initialize();
    app.Run();  // 主任务之后一直在这里处理事件，正常情况下不会返回。
}
