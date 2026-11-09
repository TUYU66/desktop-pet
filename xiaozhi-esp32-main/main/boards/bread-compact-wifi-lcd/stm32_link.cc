#include "stm32_link.h"
#include "config.h"
#include "application.h"
#include "mcp_server.h"
#include "board.h"
#include "display.h"
#include "settings.h"

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

using namespace stm32_link_detail;
namespace stm32_link_detail {

std::atomic<unsigned int> bluetooth_notice_id{0};
std::atomic<int> bluetooth_notice_result{0};
void CancelBluetoothAnnouncement() {
    const auto id = bluetooth_notice_id.exchange(0);
    if (id) Application::GetInstance().SendBluetoothControlEvent(id, false, false, true);
}

}

Stm32Link& Stm32Link::GetInstance() {
    static Stm32Link instance;
    return instance;
}

void Stm32Link::Start() {
    if (started_.exchange(true)) return;
    {
        Settings saved("stm32_ota");
        std::lock_guard<std::mutex> lock(ota_mutex_);
        ota_job_=saved.GetString("job");ota_phase_=saved.GetString("phase","idle");
        ota_version_=saved.GetString("version");ota_error_=saved.GetString("error");
        ota_progress_=ota_phase_=="completed"?100:0;
        if(saved.GetBool("pending",false)) {
            ota_phase_="unconfirmed";ota_error_="上次升级因 ESP32 重启中断，结果未知，请核对底盘状态";
        }
    }

    ESP_LOGI(kTag, "ESP32 chassis diagnostic revision=2026102601 built=%s %s", __DATE__, __TIME__);
    uart_config_t config = {};
    config.baud_rate = 115200;
    config.data_bits = UART_DATA_8_BITS;
    config.parity = UART_PARITY_DISABLE;
    config.stop_bits = UART_STOP_BITS_1;
    config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    config.source_clk = UART_SCLK_DEFAULT;
    esp_err_t error = uart_driver_install(kUart, 2048, 256, 32, &uart_events_, 0);
    const bool driver_installed = error == ESP_OK;
    if (error == ESP_OK) error = uart_param_config(kUart, &config);
    if (error == ESP_OK) {
        error = uart_set_pin(kUart, STM32_UART_TX_PIN, STM32_UART_RX_PIN,
                             UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    }
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "UART init failed: %s", esp_err_to_name(error));
        if (driver_installed) uart_driver_delete(kUart);
        started_.store(false);
        return;
    }
    uart_mutex_ = xSemaphoreCreateMutex();
    request_mutex_ = xSemaphoreCreateMutex();
    reply_semaphore_ = xSemaphoreCreateBinary();
    calibration_semaphore_ = xSemaphoreCreateBinary();
    tuning_semaphore_ = xSemaphoreCreateBinary();
    tuning_epoch_ = static_cast<int>((esp_random() & 0x7FFFFFFFU) | 1U);
    if (uart_mutex_ == nullptr || request_mutex_ == nullptr || reply_semaphore_ == nullptr || calibration_semaphore_ == nullptr || tuning_semaphore_ == nullptr) {
        ESP_LOGE(kTag, "UART synchronization creation failed");
        uart_driver_delete(kUart);
        if (uart_mutex_ != nullptr) vSemaphoreDelete(uart_mutex_);
        if (request_mutex_ != nullptr) vSemaphoreDelete(request_mutex_);
        if (reply_semaphore_ != nullptr) vSemaphoreDelete(reply_semaphore_);
        if (calibration_semaphore_ != nullptr) vSemaphoreDelete(calibration_semaphore_);
        if (tuning_semaphore_ != nullptr) vSemaphoreDelete(tuning_semaphore_);
        tuning_semaphore_ = nullptr;
        calibration_semaphore_ = nullptr;
        uart_mutex_ = nullptr;
        request_mutex_ = nullptr;
        reply_semaphore_ = nullptr;
        started_.store(false);
        return;
    }
    last_ping_tick_ = xTaskGetTickCount() - pdMS_TO_TICKS(2000);
    if (xTaskCreate(TaskEntry, "stm32_link", 4096, this, 4, nullptr) != pdPASS) {
        ESP_LOGE(kTag, "UART task creation failed");
        uart_driver_delete(kUart);
        vSemaphoreDelete(uart_mutex_);
        vSemaphoreDelete(request_mutex_);
        vSemaphoreDelete(reply_semaphore_);
        vSemaphoreDelete(calibration_semaphore_);
        vSemaphoreDelete(tuning_semaphore_);
        tuning_semaphore_ = nullptr;
        calibration_semaphore_ = nullptr;
        uart_mutex_ = nullptr;
        request_mutex_ = nullptr;
        reply_semaphore_ = nullptr;
        started_.store(false);
    }
    if (started_.load()) {
        app_bluetooth_queue_ = xQueueCreate(1, sizeof(AppBluetoothRequest));
        if (app_bluetooth_queue_ && xTaskCreate(AppBluetoothTask, "bt_app_mode", 6144, this, 2, nullptr) != pdPASS) {
            vQueueDelete(app_bluetooth_queue_); app_bluetooth_queue_ = nullptr;
        }
    }
}

void Stm32Link::AppBluetoothTask(void* context) {
    auto self = static_cast<Stm32Link*>(context);
    AppBluetoothRequest request{};
    while (xQueueReceive(self->app_bluetooth_queue_, &request, portMAX_DELAY) == pdTRUE)
        self->RunAppBluetooth(request);
    vTaskDelete(nullptr);
}

void Stm32Link::SendAppBluetoothResult(unsigned int id, bool success) {
    if (xSemaphoreTake(uart_mutex_, pdMS_TO_TICKS(1000)) != pdTRUE) return;
    char result[40];
    snprintf(result, sizeof(result), "APP_RESULT,%u,%u", id, success ? 1U : 0U);
    WriteFrame(result);
    xSemaphoreGive(uart_mutex_);
}

void Stm32Link::OnBluetoothSpeechResult(unsigned int id, bool played) {
    if (bluetooth_notice_id.load() == id && app_bluetooth_id_.load() == id)
        bluetooth_notice_result.store(played ? 1 : -1);
}

bool Stm32Link::AnnounceAppBluetooth(const AppBluetoothRequest& request, bool success) {
    if (app_bluetooth_id_.load() != request.id) return false;
    bluetooth_notice_result.store(0);
    bluetooth_notice_id.store(request.id);
    Application::GetInstance().SendBluetoothControlEvent(request.id, request.enabled, success);
    const auto deadline = xTaskGetTickCount() + pdMS_TO_TICKS(15000);
    while (app_bluetooth_id_.load() == request.id && bluetooth_notice_id.load() == request.id &&
           bluetooth_notice_result.load() == 0 && static_cast<int32_t>(deadline - xTaskGetTickCount()) > 0)
        vTaskDelay(pdMS_TO_TICKS(20));
    const bool played = app_bluetooth_id_.load() == request.id && bluetooth_notice_id.load() == request.id &&
        bluetooth_notice_result.load() == 1 && Application::GetInstance().GetAudioService().WaitForPlaybackQueueEmpty(2000);
    ESP_LOGI(kTag, "App BLE announcement id=%u enabled=%d confirmed=%d played=%d ack=%d current=%u",
             request.id, request.enabled, success, played, bluetooth_notice_result.load(), app_bluetooth_id_.load());
    if (played) bluetooth_notice_id.store(0);
    else CancelBluetoothAnnouncement();
    return played;
}

void Stm32Link::RunAppBluetooth(const AppBluetoothRequest& request) {
    if (app_bluetooth_id_.load() != request.id) return;
    if (request.enabled) {
        auto& app = Application::GetInstance();
        const auto deadline = xTaskGetTickCount() + pdMS_TO_TICKS(4000);
        bool ready = false;
        while (app_bluetooth_id_.load() == request.id) {
            const auto state = app.GetDeviceState();
            if ((state == kDeviceStateIdle || state == kDeviceStateListening) && !app.IsVoiceDetected() &&
                app.GetAudioService().WaitForPlaybackQueueEmpty(100)) {
                ready = true;
                break;
            }
            if (static_cast<int32_t>(deadline - xTaskGetTickCount()) <= 0) break;
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        if (app_bluetooth_id_.load() != request.id) return;
        if (!ready) {
            ESP_LOGW(kTag, "App BLE request id=%u busy after 4 s: state=%d voice=%d", request.id,
                     static_cast<int>(app.GetDeviceState()), app.IsVoiceDetected());
            SendAppBluetoothResult(request.id, false); return;
        }
    } else {
        const auto deadline = xTaskGetTickCount() + pdMS_TO_TICKS(5000);
        while (GetMotionSnapshot().busy && static_cast<int32_t>(deadline - xTaskGetTickCount()) > 0 &&
               app_bluetooth_id_.load() == request.id) vTaskDelay(pdMS_TO_TICKS(20));
    }
    if (app_bluetooth_id_.load() != request.id) return;
    const auto result = RequestMotion(request.enabled ? "BT,1" : "BT,0", request.id);
    const bool success = request.enabled ? result == "已经站稳，蓝牙控制已开启，可以用手机 App 操控了。" :
        result == "手机移动控制已关闭，保持站立。" || result == "手机移动控制已关闭，我已经坐下了。";
    if (app_bluetooth_id_.load() != request.id) return;
    if (!success) {
        SendAppBluetoothResult(request.id, false);
        AnnounceAppBluetooth(request, false);
        return;
    }
    // Current server TTS voice owns speech/subtitles. No embedded voice fallback.
    const bool announced = AnnounceAppBluetooth(request, true);
    if (app_bluetooth_id_.load() == request.id) SendAppBluetoothResult(request.id, announced);
}

std::string Stm32Link::GetBatteryText() const {
    if (!BatteryFresh()) {
        return "暂时读不到电量，请稍后再问我。";
    }
    const int millivolts = battery_mv_.load();
    char text[256];
    snprintf(text, sizeof(text), "电量大约还剩 %d%%。%s",
             EstimateBatteryPercent(millivolts), low_battery_.load() ? "电量不足，该充电了。" : "");
    return text;
}

bool Stm32Link::GetBatteryLevel(int& percent) const {
    if (!BatteryFresh()) return false;
    const int millivolts = battery_mv_.load();
    if (millivolts < 0) return false;
    percent = EstimateBatteryPercent(millivolts);
    return true;
}

bool Stm32Link::LinkFresh() const {
    const TickType_t tick = last_frame_tick_.load();
    return connected_.load() && static_cast<TickType_t>(xTaskGetTickCount() - tick) <= pdMS_TO_TICKS(5000);
}

int Stm32Link::GetBluetoothControlState() const {
    if (!LinkFresh() || static_cast<TickType_t>(xTaskGetTickCount() - bluetooth_tick_.load()) > pdMS_TO_TICKS(1500))
        return -1;
    return bluetooth_mode_.load();
}

bool Stm32Link::BatteryFresh() const {
    const TickType_t tick = last_battery_tick_.load();
    return LinkFresh() && status_valid_.load() && battery_mv_.load() > 0 &&
        static_cast<TickType_t>(xTaskGetTickCount() - tick) <= pdMS_TO_TICKS(3000);
}

bool Stm32Link::GetLowBattery(bool& low) const {
    if (!BatteryFresh()) return false;
    low = low_battery_.load() != 0;
    return true;
}

void Stm32Link::MarkFrame() {
    last_frame_tick_.store(xTaskGetTickCount());
    if (!connected_.exchange(true)) ESP_LOGI(kTag, "STM32 J13 connected");
}

void Stm32Link::PublishReply(uint16_t sequence, int kind, int reply) {
    // Poll and pending-id publication/retirement hold uart_mutex_. Terminal
    // results are immutable; late ACK/error frames cannot overwrite DONE.
    if (!sequence || sequence != pending_sequence_.load() ||
        (kind && kind != pending_kind_.load())) return;
    const int old = pending_reply_.load();
    if (old != kNoReply && old != kAccepted) return;
    if (old == kAccepted && reply == kAccepted) return;
    pending_reply_.store(reply);
    if (reply == kAccepted) {
        std::lock_guard<std::mutex> lock(motion_mutex_);
        request_phase_ = pending_kind_.load() == 3 && active_action_ == "bluetooth_off"
            ? "awaiting_feedback" : pending_kind_.load() == 1 || pending_kind_.load() == 3
            ? (active_action_ == "stand_up" || active_action_ == "bluetooth_on" ? "rising" : "resting") : "awaiting_feedback";
    } else if (kind == 1 || kind == 2 || kind == 3) {
        std::lock_guard<std::mutex> lock(motion_mutex_);
        // A final reply ends this action. Do not let its older non-idle DIAG
        // block the next step of a compound instruction until the next sample.
        motion_seen_ = false;
    }
    xSemaphoreGive(reply_semaphore_);
}

void Stm32Link::SetAction(const char* action, const char* phase) {
    std::lock_guard<std::mutex> lock(motion_mutex_);
    active_action_ = action;
    request_phase_ = phase;
    motion_seen_ = false; // Do not attach pre-request DIAG to the new action.
    last_result_ = "pending";
    result_tick_ = xTaskGetTickCount();
    request_busy_.store(true);
}

void Stm32Link::FinishAction(const char* result) {
    std::lock_guard<std::mutex> lock(motion_mutex_);
    last_result_ = result;
    result_tick_ = xTaskGetTickCount();
    request_phase_.clear();
    request_busy_.store(false);
}

Stm32Link::MotionSnapshot Stm32Link::GetMotionSnapshot() const {
    std::lock_guard<std::mutex> lock(motion_mutex_);
    const TickType_t now = xTaskGetTickCount();
    const bool connected = LinkFresh();
    const bool fresh = connected && motion_seen_ &&
        static_cast<TickType_t>(now - motion_tick_) <= pdMS_TO_TICKS(1500);
    const bool waiting = request_busy_.load();
    const bool hardware_busy = fresh && (motion_phase_ > 0 || motion_rest_ == 1);
    const bool result_fresh = (connected || last_result_ == "unconfirmed") &&
        static_cast<TickType_t>(now - result_tick_) <= pdMS_TO_TICKS(30000);
    MotionSnapshot snapshot{"", "unknown", result_fresh ? last_result_ : "unknown",
        waiting || hardware_busy, fresh};
    if (waiting) snapshot.action = active_action_;
    else if (hardware_busy) snapshot.action = "unknown";
    if (waiting && request_phase_ == "waiting_audio") snapshot.phase = "waiting_audio";
    else if (fresh && motion_phase_ > 0) {
        static const char* phases[] = {"idle", "rising", "settling_before", "rotating", "settling_after", "resting"};
        snapshot.phase = phases[motion_phase_];
    } else if (fresh && motion_rest_ == 1) snapshot.phase = "resting";
    else if (waiting && connected && fresh) snapshot.phase = request_phase_;
    else if (!waiting && fresh) snapshot.phase = "idle";
    return snapshot;
}

std::string Stm32Link::GetMotionHint(bool& busy) const {
    const auto state = GetMotionSnapshot();
    busy = state.busy;
    if (state.phase == "rising") return "正在起身";
    if (state.phase == "settling_before" || state.phase == "settling_after") return "等待站稳";
    if (state.phase == "rotating") return "正在转向";
    if (state.phase == "resting") return "正在休息";
    if (state.phase == "waiting_audio") return "等待播报结束";
    if (busy || state.result == "unconfirmed") return "结果待确认";
    return "";
}

std::string Stm32Link::GetStatusJson() const {
    const TickType_t now = xTaskGetTickCount();
    const bool connected = LinkFresh();
    const bool valid = BatteryFresh();
    const auto motion = GetMotionSnapshot();
    std::string posture = "unknown";
    {
        std::lock_guard<std::mutex> lock(motion_mutex_);
        const bool fresh = connected && motion_seen_ &&
            static_cast<TickType_t>(xTaskGetTickCount() - motion_tick_) <= pdMS_TO_TICKS(1500);
        const bool explicit_fresh = connected && posture_seen_ &&
            static_cast<TickType_t>(now - posture_tick_) <= pdMS_TO_TICKS(1500);
        if (explicit_fresh) {
            static const char* names[] = {"unknown", "standing", "seated", "rising", "resting", "fault"};
            posture = names[posture_state_];
        } else if (!posture_seen_ && fresh && !motion.busy && motion_phase_ == 0 && motion_error_ == 0) {
            if (motion_stopped_ && motion_rest_ == 2) posture = "seated";
            else if (!motion_stopped_ && motion_rest_ == 0) posture = "standing";
        }
    }
    const bool bluetooth_fresh = connected && bluetooth_mode_.load() >= 0 &&
        static_cast<TickType_t>(xTaskGetTickCount() - bluetooth_tick_.load()) <= pdMS_TO_TICKS(1500);
    return std::string("{\"connected\":") + (connected ? "true" : "false") +
           ",\"posture\":\"" + posture + "\"" +
           ",\"statusValid\":" + (valid ? "true" : "false") +
           ",\"batteryMv\":" + std::to_string(valid ? battery_mv_.load() : -1) +
           ",\"batteryPercent\":" + std::to_string(valid ? EstimateBatteryPercent(battery_mv_.load()) : -1) +
           ",\"balanceStopped\":" + (valid ? (balance_stopped_.load() ? "true" : "false") : "null") +
           ",\"lowBattery\":" + (valid ? (low_battery_.load() ? "true" : "false") : "null") +
           ",\"activeAction\":\"" + motion.action + "\",\"busy\":" + (motion.busy ? "true" : "false") +
           ",\"phase\":\"" + motion.phase + "\",\"lastResult\":\"" + motion.result +
           "\",\"motionValid\":" + (motion.valid ? "true" : "false") + ",\"bluetoothMode\":" +
           (bluetooth_fresh ? (bluetooth_mode_.load() ? "true" : "false") : "null") + ",\"calibration\":" + GetCalibrationJson() + "}";
}
