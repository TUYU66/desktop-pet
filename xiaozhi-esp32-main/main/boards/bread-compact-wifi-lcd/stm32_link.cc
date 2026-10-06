#include "stm32_link.h"
#include "config.h"
#include "application.h"
#include "link_frame.h"
#include "mcp_server.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cerrno>
#include <memory>
#include <esp_random.h>
#include <nvs.h>
#include <driver/uart.h>
#include <esp_err.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace {
constexpr uart_port_t kUart = UART_NUM_1;
constexpr char kTag[] = "Stm32Link";
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

Stm32Link& Stm32Link::GetInstance() {
    static Stm32Link instance;
    return instance;
}

void Stm32Link::Start() {
    if (started_.exchange(true)) return;

    ESP_LOGI(kTag, "ESP32 chassis diagnostic revision=2026100501 built=%s %s", __DATE__, __TIME__);
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

std::string Stm32Link::GetCalibrationJson() const {
    std::lock_guard<std::mutex> lock(calibration_mutex_);
    const bool fresh = LinkFresh() && calibration_supported_.load() &&
        static_cast<TickType_t>(xTaskGetTickCount() - calibration_tick_.load()) <= pdMS_TO_TICKS(1500);
    std::string json = std::string("{\"supported\":") + (calibration_supported_.load() ? "true" : "false") +
        ",\"valid\":" + (fresh ? "true" : "false");
    static const char* keys[] = {"tick", "active", "session", "wheel", "pwm", "leftCounts", "rightCounts",
        "leftTravel", "rightTravel", "reason", "runMs", "batteryMv"};
    for (size_t i = 0; i < 12; ++i) json += std::string(",\"") + keys[i] + "\":" +
        (fresh ? std::to_string(calibration_fields_[i]) : "null");
    return json + "}";
}

std::string Stm32Link::RequestCalibration(const std::string& operation, int wheel, int pwm, bool confirmed, unsigned int expected_session, unsigned int sample_tick) {
    auto result = [this](int code) {
        return std::string("{\"ok\":") + (code == 0 ? "true" : "false") +
            ",\"errorCode\":" + std::to_string(code) + ",\"state\":" + GetCalibrationJson() + "}";
    };
    const bool arm = operation == "arm", set = operation == "set", exit = operation == "exit";
    if ((!arm && !set && !exit) || wheel < 1 || wheel > 2 || pwm < -2600 || pwm > 2600) return result(7);
    if (arm && !confirmed) return result(9);
    if (!started_.load() || !request_mutex_ || !uart_mutex_ || !calibration_semaphore_) return result(1);
    if (xSemaphoreTake(request_mutex_, pdMS_TO_TICKS(30)) != pdTRUE) return result(5);
    auto release = [this](void*) { xSemaphoreGive(request_mutex_); };
    std::unique_ptr<void, decltype(release)> lease(this, release);
    if (!exit && (!LinkFresh() || !calibration_supported_.load() ||
        static_cast<TickType_t>(xTaskGetTickCount() - calibration_tick_.load()) > pdMS_TO_TICKS(1500))) return result(1);
    if (arm && GetMotionSnapshot().busy) return result(5);
    if (set && (!calibration_active_.load() || expected_session == 0 || expected_session != calibration_session_.load())) return result(6);
    if (set) {
        std::lock_guard<std::mutex> lock(calibration_mutex_);
        const uint32_t observed_tick = static_cast<uint32_t>(calibration_fields_[0]);
        if (((observed_tick - sample_tick) & 0x7FFFFFFFU) >= 1500U)
            return "{\"ok\":false,\"errorCode\":6}";
    }
    if (!McpServer::GetInstance().CurrentCallIsValid()) return result(10);
    uint16_t sequence = next_sequence_.fetch_add(1);
    if (!sequence) sequence = next_sequence_.fetch_add(1);
    char request[64];
    if (arm) snprintf(request, sizeof(request), "CAL,ARM,%u", static_cast<unsigned int>(sequence));
    else if (exit) snprintf(request, sizeof(request), "CAL,EXIT,%u", static_cast<unsigned int>(sequence));
    else snprintf(request, sizeof(request), "CAL,SET,%u,%u,%d,%d,%u", calibration_session_.load(),
                  static_cast<unsigned int>(sequence), wheel, pwm, sample_tick);
    if (xSemaphoreTake(uart_mutex_, pdMS_TO_TICKS(50)) != pdTRUE) return result(5);
    while (xSemaphoreTake(calibration_semaphore_, 0) == pdTRUE) {}
    calibration_reply_.store(-1);
    calibration_pending_.store(sequence);
    const int sent = WriteFrame(request);
    xSemaphoreGive(uart_mutex_);
    if (sent <= 0) { calibration_pending_.store(0); return result(10); }
    const bool replied = xSemaphoreTake(calibration_semaphore_, pdMS_TO_TICKS(650)) == pdTRUE;
    calibration_pending_.store(0);
    const int code = replied ? calibration_reply_.load() : 10;
    if (code != 0) return result(code);
    {
        std::lock_guard<std::mutex> lock(calibration_mutex_);
        if (static_cast<TickType_t>(xTaskGetTickCount() - calibration_tick_.load()) > pdMS_TO_TICKS(1500) ||
            (arm && (!calibration_active_.load() || calibration_session_.load() != sequence)) ||
            (exit && (calibration_active_.load() || calibration_fields_[4] != 0)) ||
            (set && (!calibration_active_.load() || calibration_fields_[3] != wheel || calibration_fields_[4] != pwm)))
            return "{\"ok\":false,\"errorCode\":10}";
    }
    return result(0);
}

std::string Stm32Link::GetTuningJson(int after, int epoch) const {
    const auto saved=ReadSavedTuning();
    int64_t saved_values[6]{};
    std::lock_guard<std::mutex> lock(tuning_mutex_);
    const auto now=xTaskGetTickCount();
    const bool valid=LinkFresh() && tuning_seen_ && static_cast<TickType_t>(now-tuning_tick_)<=pdMS_TO_TICKS(1500);
    const bool live_valid=LinkFresh() && live_seen_ && static_cast<TickType_t>(now-live_tick_)<=pdMS_TO_TICKS(1500);
    const bool gyro_valid=live_valid && gyro_seen_ && gyro_fields_[12]==tuning_fields_[3] && static_cast<TickType_t>(now-gyro_tick_)<=pdMS_TO_TICKS(1500);
    std::string gyro=std::string("{\"supported\":")+(gyro_seen_?"true":"false")+
        ",\"valid\":"+(gyro_valid?"true":"false")+",\"revision\":"+std::to_string(gyro_fields_[1])+
        ",\"status\":"+std::to_string(gyro_fields_[2])+",\"error\":"+std::to_string(gyro_fields_[3])+
        ",\"progress\":"+std::to_string(gyro_fields_[4])+",\"calibrated\":"+(gyro_fields_[5]?"true":"false")+
        ",\"pitchBias\":"+std::to_string(gyro_fields_[6]/100.0)+",\"yawBias\":"+std::to_string(gyro_fields_[7]/100.0)+
        ",\"rawPitch\":"+std::to_string(gyro_fields_[8])+",\"rawYaw\":"+std::to_string(gyro_fields_[9])+
        ",\"pitchCorrected\":"+std::to_string(gyro_fields_[10]/100.0)+",\"yawCorrected\":"+std::to_string(gyro_fields_[11]/100.0)+"}";
    const bool reset=epoch!=tuning_epoch_ || after>live_cursor_;
    const bool gap=!reset && after>0 && !live_points_.empty() && after<live_points_.front().cursor-1;
    std::string json=std::string("{\"supported\":")+(tuning_seen_?"true":"false")+
        ",\"valid\":"+(valid?"true":"false")+",\"telemetryValid\":"+(live_valid?"true":"false")+
        ",\"epoch\":"+std::to_string(tuning_epoch_)+",\"tick\":"+std::to_string(tuning_fields_[2])+
        ",\"revision\":"+std::to_string(tuning_fields_[3])+",\"writable\":"+(valid&&tuning_fields_[4]?"true":"false")+
        ",\"hasPrevious\":"+(valid&&tuning_fields_[5]?"true":"false")+
        ",\"params\":"+(valid?TuningParamsJson(tuning_fields_+6):"null")+
        ",\"saved\":"+(!saved.empty()&&ParseFields(saved.c_str(),saved_values,6)?TuningParamsJson(saved_values):"null")+
        ",\"gyro\":"+gyro+",\"reset\":"+(reset?"true":"false")+",\"gap\":"+(gap?"true":"false")+",\"points\":[";
    int count=0, cursor=reset?0:after;
    // Bounded response: newest history on first load, incremental thereafter.
    const int start=reset && live_points_.size()>30?static_cast<int>(live_points_.size())-30:0;
    for (size_t n=static_cast<size_t>(start);n<live_points_.size() && count<30;++n) {
        const auto& point=live_points_[n];
        if (!reset && point.cursor<=after) continue;
        if (count++) json+=",";
        json+="["+std::to_string(point.cursor);
        for (auto value:point.fields) json+=","+std::to_string(value);
        json+="]"; cursor=point.cursor;
    }
    return json+"],\"cursor\":"+std::to_string(cursor)+"}";
}

std::string Stm32Link::RequestTuning(const std::string& operation, unsigned int revision,
                                    unsigned int sample_tick, const std::string& values) {
    auto result=[this](int code) {
        return std::string("{\"ok\":")+(code==0?"true":"false")+",\"errorCode\":"+std::to_string(code)+
            ",\"state\":"+GetTuningJson()+"}";
    };
    const bool apply=operation=="apply", undo=operation=="undo", save=operation=="save", load=operation=="load";
    const bool gyro_start=operation=="gyro_start", gyro_cancel=operation=="gyro_cancel", gyro_clear=operation=="gyro_clear";
    const bool gyro=gyro_start||gyro_cancel||gyro_clear;
    if (!apply && !undo && !save && !load && !gyro) return result(6);
    std::string target=load?ReadSavedTuning():values;
    if (target.size()>111) return result(3);
    int64_t proposed[6]{};
    if ((apply||load) && (!ParseFields(target.c_str(),proposed,6)||!TuningValuesValid(proposed))) return result(3);
    if (apply||load) {
        target.clear();
        for (int i=0;i<6;++i) { if (i) target+=","; target+=std::to_string(proposed[i]); }
    }
    if (!request_mutex_ || !uart_mutex_ || !tuning_semaphore_) return result(1);
    if (xSemaphoreTake(request_mutex_,pdMS_TO_TICKS(30))!=pdTRUE) return result(2);
    auto release=[this](void*) { xSemaphoreGive(request_mutex_); };
    std::unique_ptr<void,decltype(release)> lease(this,release);
    {
        std::lock_guard<std::mutex> lock(tuning_mutex_);
        if (!LinkFresh() || !tuning_seen_ || static_cast<TickType_t>(xTaskGetTickCount()-tuning_tick_)>pdMS_TO_TICKS(1500)) return "{\"ok\":false,\"errorCode\":1}";
        if (tuning_fields_[3]!=revision) return "{\"ok\":false,\"errorCode\":4}";
        if (gyro && (!gyro_seen_ || static_cast<TickType_t>(xTaskGetTickCount()-gyro_tick_)>pdMS_TO_TICKS(1500)))
            return "{\"ok\":false,\"errorCode\":1}";
    }
    if (GetMotionSnapshot().busy || calibration_active_.load()) return result(2);
    if (!McpServer::GetInstance().CurrentCallIsValid()) return result(7);
    uint16_t seq=next_sequence_.fetch_add(1);
    if (!seq) seq=next_sequence_.fetch_add(1);
    char request[128];
    snprintf(request,sizeof(request),"TUNE,%s,%u,%u,%u",undo?"UNDO":save?"CHECK":gyro_start?"GSTART":gyro_cancel?"GCANCEL":gyro_clear?"GCLEAR":"SET",seq,revision,sample_tick);
    if (apply||load) {
        const size_t used=strlen(request);
        snprintf(request+used,sizeof(request)-used,",%s",target.c_str());
    }
    if (xSemaphoreTake(uart_mutex_,pdMS_TO_TICKS(50))!=pdTRUE) return result(2);
    while (xSemaphoreTake(tuning_semaphore_,0)==pdTRUE) {}
    tuning_pending_.store(seq);
    const int sent=WriteFrame(request);
    xSemaphoreGive(uart_mutex_);
    if (sent<=0) { tuning_pending_.store(0); return result(7); }
    const bool replied=xSemaphoreTake(tuning_semaphore_,pdMS_TO_TICKS(800))==pdTRUE;
    tuning_pending_.store(0);
    if (!replied) return result(7); // Never retry a mutation after timeout.
    int64_t ack[12]{};
    {
        std::lock_guard<std::mutex> lock(tuning_mutex_);
        for (int i=0;i<12;++i) ack[i]=tuning_ack_fields_[i];
    }
    if (ack[0]!=seq) return result(7);
    if (ack[1]) return result(static_cast<int>(ack[1]));
    const bool same_revision=save;
    if (ack[3]!=(same_revision?revision:revision>=2147483646U?1:revision+1)) return result(7);
    if (gyro) {
        std::lock_guard<std::mutex> lock(tuning_mutex_);
        if (!gyro_seen_ || gyro_fields_[12]!=ack[3] || static_cast<TickType_t>(xTaskGetTickCount()-gyro_tick_)>pdMS_TO_TICKS(1500) ||
            (gyro_start && gyro_fields_[2]!=1) || (gyro_cancel && gyro_fields_[2]==1) ||
            (gyro_clear && (gyro_fields_[2]!=0 || gyro_fields_[5]!=0)))
            return "{\"ok\":false,\"errorCode\":7}";
    }
    if (apply||load) for (int i=0;i<6;++i) if (ack[i+6]!=proposed[i]) return result(7);
    if (save) {
        std::string csv;
        for (int i=6;i<12;++i) { if (i!=6) csv+=","; csv+=std::to_string(ack[i]); }
        nvs_handle_t handle;
        if (nvs_open("chassis_tune",NVS_READWRITE,&handle)!=ESP_OK) return result(8);
        esp_err_t err=nvs_set_str(handle,"values_v1",csv.c_str());
        if (err==ESP_OK) err=nvs_commit(handle);
        nvs_close(handle);
        if (err!=ESP_OK || ReadSavedTuning()!=csv) return result(8);
    }
    return result(0);
}

std::string Stm32Link::RequestStand() {
    return RequestMotion("STAND");
}

std::string Stm32Link::RequestBluetooth(bool enabled) {
    return RequestMotion(enabled ? "BT,1" : "BT,0");
}

std::string Stm32Link::RequestRest() {
    return RequestMotion("REST");
}

std::string Stm32Link::RequestTurnLeft() {
    return RequestMotion("TURN,L");
}

std::string Stm32Link::RequestTurnRight() {
    return RequestMotion("TURN,R");
}

std::string Stm32Link::RequestTurnAround() {
    return RequestMotion("TURN,B");
}

std::string Stm32Link::RequestMotion(const char* command) {
    const bool standing = strcmp(command, "STAND") == 0;
    const bool turning = strncmp(command, "TURN,", 5) == 0;
    if (!started_.load() || uart_mutex_ == nullptr || request_mutex_ == nullptr ||
        reply_semaphore_ == nullptr) {
        ESP_LOGE(kTag, "%s request not sent: UART is not initialized", command);
        return "我现在还动不了，动作控制还没准备好。";
    }
    if (xSemaphoreTake(request_mutex_, 0) != pdTRUE) {
        return "我正在处理上一个动作，稍等一下。";
    }

    // Release on every return/exception. Never release the hardware slot merely
    // because speech was interrupted or the WebSocket disappeared.
    auto release = [this](void*) {
        xSemaphoreTake(uart_mutex_, portMAX_DELAY);
        pending_sequence_.store(0);
        pending_kind_.store(0);
        xSemaphoreGive(uart_mutex_);
        if (request_busy_.load()) FinishAction("unconfirmed");
        xSemaphoreGive(request_mutex_);
    };
    std::unique_ptr<void, decltype(release)> lease(this, release);
    if (calibration_active_.load()) return "正在进行电机校准，退出校准后再移动。";
    {
        std::lock_guard<std::mutex> lock(tuning_mutex_);
        if (gyro_seen_ && gyro_fields_[2]==1 && static_cast<TickType_t>(xTaskGetTickCount()-gyro_tick_)<=pdMS_TO_TICKS(1500))
            return "正在采集静止零偏，请等待校准结束后再移动。";
    }
    if (!LinkFresh()) return "暂时联系不上底盘，等连接恢复再试。";
    if (GetMotionSnapshot().busy) return "我还在做上一个动作，等完成再试。";
    const char* action = standing ? "stand_up" : strcmp(command, "REST") == 0 ? "rest" :
        strcmp(command, "TURN,L") == 0 ? "turn_left" : strcmp(command, "TURN,R") == 0 ? "turn_right" :
        strcmp(command, "TURN,B") == 0 ? "turn_around" : strcmp(command, "BT,1") == 0 ? "bluetooth_on" : "bluetooth_off";
    SetAction(action, "waiting_audio");

    // The server sends motion only after its TTS stop. Wait for the last
    // decoded frame to leave the local speaker before writing to USART2.
    const bool bluetooth = strncmp(command, "BT,", 3) == 0;
    const bool disabling_bluetooth = strcmp(command, "BT,0") == 0;
    if (!disabling_bluetooth && !Application::GetInstance().GetAudioService().WaitForPlaybackQueueEmpty(3000)) {
        FinishAction("not_sent");
        ESP_LOGW(kTag, "%s request cancelled: speaker did not finish in 3 s", command);
        return "我还没说完，这次先不动啦。";
    }
    if (!disabling_bluetooth) vTaskDelay(pdMS_TO_TICKS(100));

    // The STM32 protocol accepts 0..65535; reserve 0 for "no pending request".
    uint16_t sequence = next_sequence_.fetch_add(1);
    if (sequence == 0) sequence = next_sequence_.fetch_add(1);
    char request[24];
    snprintf(request, sizeof(request), "%s,%u", command, static_cast<unsigned int>(sequence));
    // Pending publication and retirement are serialized with the RX task.
    if (xSemaphoreTake(uart_mutex_, pdMS_TO_TICKS(1000)) != pdTRUE) {
        FinishAction("not_sent");
        return "我正在处理上一个动作，稍等一下。";
    }
    if (!McpServer::GetInstance().CurrentCallIsValid() || !LinkFresh()) {
        xSemaphoreGive(uart_mutex_);
        FinishAction("not_sent");
        return "连接已变化，这次没有发送动作。";
    }
    xSemaphoreTake(reply_semaphore_, 0);
    pending_reply_.store(kNoReply);
    pending_kind_.store(turning ? 2 : bluetooth ? 3 : 1);
    pending_sequence_.store(sequence);
    {
        std::lock_guard<std::mutex> lock(motion_mutex_);
        request_phase_ = "awaiting_confirmation";
        motion_seen_ = false;
    }
    const int written = WriteFrame(request);
    xSemaphoreGive(uart_mutex_);
    ESP_LOGI(kTag, "TX %s request %u (%d bytes)", command,
             static_cast<unsigned int>(sequence), written);

    std::string result;
    const char* outcome = "unconfirmed";
    if (written < 0) {
        result = "我没能发出动作指令，这次先不动了。";
    } else {
        const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(turning ? 50000 : 12000);
        xSemaphoreTake(reply_semaphore_, pdMS_TO_TICKS(1000));
        int reply = pending_reply_.load();
        bool accepted = reply == kAccepted;
        unsigned int queries = 0;
        if (turning && reply == kNoReply) {
            ESP_LOGW(kTag, "TURN %u ACK not received; retaining request ID for final result, no retransmit", sequence);
        }
        // A lost ACK must not make us discard a later DONE/ABORT. Keep the
        // request lock and sequence until a terminal reply or the total deadline.
        while (reply == kAccepted || reply == kNoReply) {
            const int32_t remaining = static_cast<int32_t>(deadline - xTaskGetTickCount());
            if (remaining <= 0) {
                reply = kNoReply;
                break;
            }
            const TickType_t wait = static_cast<TickType_t>(remaining) < pdMS_TO_TICKS(2000)
                ? static_cast<TickType_t>(remaining) : pdMS_TO_TICKS(2000);
            xSemaphoreTake(reply_semaphore_, wait);
            reply = pending_reply_.load();
            accepted = accepted || reply == kAccepted;
            if ((reply == kAccepted || reply == kNoReply) &&
                static_cast<int32_t>(deadline - xTaskGetTickCount()) > 0 &&
                LinkFresh() && xSemaphoreTake(uart_mutex_, pdMS_TO_TICKS(20)) == pdTRUE) {
                char query[32];
                snprintf(query, sizeof(query), "%s,%u", turning ? "TURN_RESULT" : "POSTURE_RESULT", static_cast<unsigned int>(sequence));
                if (WriteFrame(query) > 0) ++queries; // Read-only recovery; never resend TURN.
                xSemaphoreGive(uart_mutex_);
            }
        }
        // Seal this request before deriving the user-facing result. Replies
        // after the deadline belong only to diagnostic history, not a new call.
        xSemaphoreTake(uart_mutex_, portMAX_DELAY);
        if (reply == kNoReply || reply == kUnconfirmed) {
            int last_phase, last_error;
            TickType_t diag_age;
            {
                std::lock_guard<std::mutex> lock(motion_mutex_);
                last_phase = motion_phase_;
                last_error = motion_error_;
                diag_age = xTaskGetTickCount() - motion_tick_;
            }
            ESP_LOGW(kTag, "%s unconfirmed: id=%u ack=%d queries=%u link=%d last_phase=%d error=%d diag_age_ms=%lu crc=%lu framing=%lu fifo=%lu buffer=%lu",
                     command, static_cast<unsigned int>(sequence), accepted, queries, LinkFresh(),
                     last_phase, last_error, static_cast<unsigned long>(pdTICKS_TO_MS(diag_age)), static_cast<unsigned long>(crc_bad_),
                     static_cast<unsigned long>(framing_bad_), static_cast<unsigned long>(fifo_overflow_),
                     static_cast<unsigned long>(buffer_full_));
        }
        pending_sequence_.store(0);
        pending_kind_.store(0);
        xSemaphoreGive(uart_mutex_);
        outcome = reply == kCompleted || reply == kPostureCompleted || reply == kBluetoothCompleted ||
            reply == kBluetoothStandingCompleted || reply == kBluetoothRestingCompleted ? "completed" :
            reply == kNoReply || reply == kUnconfirmed ? "unconfirmed" : "failed";
        switch (reply) {
        case kBluetoothCompleted:
            result = strcmp(command, "BT,1") == 0
                ? "已经站稳，蓝牙控制已开启，可以用手机 App 操控了。"
                : "手机移动控制已关闭。";
            break;
        case kBluetoothStandingCompleted: result = "手机移动控制已关闭，保持站立。"; break;
        case kBluetoothRestingCompleted: result = "手机移动控制已关闭，我已经坐下了。"; break;
        case kBluetoothStandFailed: result = strcmp(command, "BT,1") == 0
            ? "起立还没完成，蓝牙控制没有开启。" : "手机移动控制已关闭，目前还没站稳。"; break;
        case kBluetoothRestFailed: result = "蓝牙控制已关闭，不过坐下还没完成，请先看看我的姿态。"; break;
        case kAccepted:
            result = "收到指令，还没有完成确认。";
            break;
        case kCompleted:
            result = strcmp(command, "TURN,L") == 0 ? "好，我向左转过来啦。" :
                     strcmp(command, "TURN,R") == 0 ? "好，我向右转过来啦。" :
                     "好，我转过身来啦。";
            break;
        case kPostureCompleted:
            result = standing ? "起立完成。" : "休息完成。";
            break;
        case kAborted: result = "这次动作没能做完。"; break;
        case kSettleAfter: result = "已经转过来了，不过还没稳住。"; break;
        case kNotStable: result = "我还有点晃，等稳一点再转。"; break;
        case kUnconfirmed: result = "底盘没有这次转向的确认记录，动作结果未确认，请先检查我的状态。"; break;
        case kNotReady: result = "我还没准备好，稍等一下再试。"; break;
        case kLowBattery: result = "我电量太低了，先不动啦。"; break;
        case kAngle: result = "我现在歪得有点厉害，扶稳了再试。"; break;
        case kNotStanding: result = turning ? "我还没靠稳，先帮我看看是不是坐在支架上。" :
                                               "我还没站起来，暂时没法后靠休息。"; break;
        case kFault: result = "我刚才的动作触发了保护，先帮我检查一下。"; break;
        case kBusy: result = "我还在做上一个动作，等完成再试。"; break;
        case kBluetoothActive: result = "现在由手机控制，先关闭蓝牙控制再让我做动作吧。"; break;
        case kNoReply: result = turning ? "我没收到转向完成确认，请先看看我的状态，别连续重试。"
                                       : "我没收到动作确认，不确定有没有执行，请先看看我的状态。"; break;
        default: result = "我这次没能动起来，先别连续重试。"; break;
        }
    }
    FinishAction(outcome);
    ESP_LOGI(kTag, "%s request %u: %s", command,
             static_cast<unsigned int>(sequence), result.c_str());
    return result;
}

std::string Stm32Link::GetStatusJson() const {
    const bool connected = LinkFresh();
    const bool valid = BatteryFresh();
    const auto motion = GetMotionSnapshot();
    std::string posture = "unknown";
    {
        std::lock_guard<std::mutex> lock(motion_mutex_);
        const bool fresh = connected && motion_seen_ &&
            static_cast<TickType_t>(xTaskGetTickCount() - motion_tick_) <= pdMS_TO_TICKS(1500);
        if (fresh && !motion.busy && motion_phase_ == 0 && motion_error_ == 0) {
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

void Stm32Link::TaskEntry(void* context) {
    auto* link = static_cast<Stm32Link*>(context);
    while (true) {
        link->Poll();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void Stm32Link::Poll() {
    if (uart_mutex_ == nullptr || xSemaphoreTake(uart_mutex_, pdMS_TO_TICKS(20)) != pdTRUE) return;
    uart_event_t event;
    bool damaged = false;
    for (int i = 0; i < 32 && xQueueReceive(uart_events_, &event, 0) == pdTRUE; ++i) {
        if (event.type == UART_FIFO_OVF) ++fifo_overflow_;
        if (event.type == UART_BUFFER_FULL) ++buffer_full_;
        if (event.type == UART_FRAME_ERR) ++uart_frame_error_;
        if (event.type == UART_PARITY_ERR) ++parity_error_;
        if (event.type == UART_FIFO_OVF || event.type == UART_BUFFER_FULL ||
            event.type == UART_FRAME_ERR || event.type == UART_PARITY_ERR) {
            ESP_LOGW(kTag, "UART RX error type=%d (FIFO=%d buffer=%d frame=%d parity=%d)",
                     event.type, UART_FIFO_OVF, UART_BUFFER_FULL, UART_FRAME_ERR, UART_PARITY_ERR);
            damaged = true;
        }
    }
    if (damaged) {
        // Drop uncertain bytes; never infer an ACK from a damaged fragment.
        uart_flush_input(kUart);
        rx_length_ = 0;
        rx_overflow_ = true; // Resynchronize at the next @ marker.
        status_valid_.store(false);
    }
    const TickType_t now = xTaskGetTickCount();
    if (static_cast<TickType_t>(now - last_ping_tick_) >= pdMS_TO_TICKS(2000)) {
        WriteFrame("PING");
        last_ping_tick_ = now;
    }
    uint8_t bytes[256];
    for (int batch = 0; batch < 4; ++batch) {
        const int count = uart_read_bytes(kUart, bytes, sizeof(bytes), 0);
        if (count <= 0) break;
        for (int i = 0; i < count; ++i) FeedByte(bytes[i]);
    }
    // FeedByte can publish timestamps newer than the time captured before reading.
    const TickType_t checked_at = xTaskGetTickCount();
    if ((crc_bad_ || framing_bad_ || fifo_overflow_ || buffer_full_ || uart_frame_error_ || parity_error_ || resync_) &&
        static_cast<TickType_t>(checked_at - last_link_report_tick_) >= pdMS_TO_TICKS(5000)) {
        ESP_LOGW(kTag, "UART v2 crc_bad=%lu framing_bad=%lu fifo_overflow=%lu buffer_full=%lu uart_frame_error=%lu parity_error=%lu resync=%lu",
                 (unsigned long)crc_bad_, (unsigned long)framing_bad_, (unsigned long)fifo_overflow_,
                 (unsigned long)buffer_full_, (unsigned long)uart_frame_error_, (unsigned long)parity_error_, (unsigned long)resync_);
        crc_bad_ = framing_bad_ = fifo_overflow_ = buffer_full_ = uart_frame_error_ = parity_error_ = resync_ = 0;
        last_link_report_tick_ = checked_at;
    }
    if (connected_.load() && static_cast<TickType_t>(checked_at - last_frame_tick_) > pdMS_TO_TICKS(5000)) {
        connected_.store(false);
        ESP_LOGW(kTag, "STM32 J13 disconnected: no UART frames for 5 s");
    }
    if (status_valid_.load() && static_cast<TickType_t>(checked_at - last_battery_tick_) > pdMS_TO_TICKS(3000)) {
        status_valid_.store(false);
    }
    xSemaphoreGive(uart_mutex_);
}

void Stm32Link::FeedByte(uint8_t byte) {
    if (byte == '@') {
        if (!rx_overflow_ && rx_length_ != 0) ++resync_;
        rx_length_ = 0;
        rx_overflow_ = false;
        return;
    }
    if (byte == '\n') {
        if (!rx_overflow_ && rx_length_ != 0) {
            const int decoded = link_decode(rx_line_, rx_length_);
            if (decoded == 1) ProcessLine(rx_line_);
            else if (decoded == -1) ++crc_bad_;
            else ++framing_bad_;
        }
        rx_length_ = 0;
        rx_overflow_ = true;
        return;
    }
    if (rx_overflow_) return;
    if (rx_length_ < sizeof(rx_line_) - 1) rx_line_[rx_length_++] = static_cast<char>(byte);
    else {
        rx_overflow_ = true;
        ++framing_bad_;
    }
}

void Stm32Link::ProcessLine(const char* line) {
    const TickType_t now = xTaskGetTickCount();
    unsigned int turn_sequence = 0;
    char extra = 0;
    unsigned int unknown_sequence = 0;
    if (sscanf(line, "TURN_UNKNOWN,%u%c", &unknown_sequence, &extra) == 1 &&
        unknown_sequence > 0 && unknown_sequence <= 65535) {
        MarkFrame();
        ESP_LOGW(kTag, "STM32 TURN result unknown: id=%u pending=%u", unknown_sequence,
                 static_cast<unsigned int>(pending_sequence_.load()));
        PublishReply(unknown_sequence, 2, kUnconfirmed);
        return;
    }
    unsigned int bluetooth_id = 0, bluetooth_enabled = 0;
    int bluetooth_pose = -1; // Legacy replies cannot prove the final posture.
    int64_t bluetooth_fields[4]{};
    char bluetooth_error[8]{};
    bool bluetooth_done = false;
    if (strncmp(line, "BT_DONE,", 8) == 0) {
        const bool extended = ParseFields(line + 8, bluetooth_fields, 3);
        const bool legacy = !extended && ParseFields(line + 8, bluetooth_fields, 2);
        bluetooth_done = (extended || legacy) && bluetooth_fields[0] > 0 && bluetooth_fields[0] <= 65535 &&
            bluetooth_fields[1] >= 0 && bluetooth_fields[1] <= 1 &&
            (!extended || (bluetooth_fields[2] >= 0 && bluetooth_fields[2] <= 2 &&
                           (!bluetooth_fields[1] || bluetooth_fields[2] == 0)));
        if (bluetooth_done) {
            bluetooth_id = static_cast<unsigned int>(bluetooth_fields[0]);
            bluetooth_enabled = static_cast<unsigned int>(bluetooth_fields[1]);
            if (extended) bluetooth_pose = static_cast<int>(bluetooth_fields[2]);
        }
    }
    const bool bluetooth_aborted = !bluetooth_done &&
        sscanf(line, "BT_ABORT,%u,%7[A-Z]%c", &bluetooth_id, bluetooth_error, &extra) == 2;
    if (bluetooth_id && bluetooth_id <= 65535 &&
        ((bluetooth_done && bluetooth_enabled <= 1) || (bluetooth_aborted &&
         (strcmp(bluetooth_error, "STAND") == 0 || strcmp(bluetooth_error, "REST") == 0)))) {
        MarkFrame();
        int reply = bluetooth_done ? kBluetoothCompleted :
            strcmp(bluetooth_error, "STAND") == 0 ? kBluetoothStandFailed : kBluetoothRestFailed;
        if (bluetooth_done && !bluetooth_enabled && bluetooth_pose == 0) reply = kBluetoothStandingCompleted;
        if (bluetooth_done && !bluetooth_enabled && bluetooth_pose == 1) reply = kBluetoothRestingCompleted;
        if (bluetooth_id == pending_sequence_.load() && pending_kind_.load() == 3) {
            bluetooth_mode_.store(bluetooth_done ? bluetooth_enabled : 0);
            bluetooth_tick_.store(now);
            std::lock_guard<std::mutex> lock(motion_mutex_);
            if (bluetooth_done && bluetooth_enabled != static_cast<unsigned int>(active_action_ == "bluetooth_on"))
                reply = kUnknownError;
        }
        PublishReply(bluetooth_id, 3, reply);
        return;
    }
    unsigned int posture_id = 0;
    const bool posture_done = sscanf(line, "POSTURE_DONE,%u%c", &posture_id, &extra) == 1;
    const bool posture_aborted = !posture_done && sscanf(line, "POSTURE_ABORT,%u%c", &posture_id, &extra) == 1;
    if ((posture_done || posture_aborted) && posture_id && posture_id <= 65535) {
        MarkFrame();
        PublishReply(posture_id, 1, posture_done ? kPostureCompleted : kAborted);
        return;
    }
    char turn_error[24] = {};
    const bool turn_done = sscanf(line, "TURN_DONE,%u%c", &turn_sequence, &extra) == 1;
    const bool turn_aborted = !turn_done &&
        (sscanf(line, "TURN_ABORT,%u,%23[A-Z_]%c", &turn_sequence, turn_error, &extra) == 2 ||
         sscanf(line, "TURN_ABORT,%u%c", &turn_sequence, &extra) == 1);
    if ((turn_done || turn_aborted) && turn_sequence && turn_sequence <= 65535) {
        MarkFrame();
        if (turn_aborted) ESP_LOGW(kTag, "STM32 turn %u aborted: %s", turn_sequence,
                                   turn_error[0] ? turn_error : "UNSPECIFIED");
        else ESP_LOGI(kTag, "STM32 turn %u completed", turn_sequence);
        if (turn_sequence != pending_sequence_.load())
            ESP_LOGW(kTag, "Late/unmatched TURN result: sequence=%u pending=%u", turn_sequence,
                     static_cast<unsigned int>(pending_sequence_.load()));
        PublishReply(turn_sequence, 2, turn_done ? kCompleted :
            strcmp(turn_error, "SETTLE_AFTER") == 0 ? kSettleAfter :
            strcmp(turn_error, "NOT_STABLE") == 0 ? kNotStable : kAborted);
        return;
    }
    if (strcmp(line, "REST_DONE") == 0 || strcmp(line, "REST_ABORT") == 0 ||
        strcmp(line, "REST_FAULT") == 0) {
        MarkFrame();
        if (strcmp(line, "REST_DONE") == 0) {
            balance_stopped_.store(1);
            ESP_LOGI(kTag, "REST completed: motors stopped for rear support");
        } else if (strcmp(line, "REST_FAULT") == 0) {
            balance_stopped_.store(1);
            ESP_LOGE(kTag, "REST stopped: IMU update timeout");
        } else {
            ESP_LOGW(kTag, "REST canceled: rear tilt was not reached safely");
        }
        return;
    }
    if (strncmp(line,"GSTATE,",7)==0) {
        int64_t values[13]{};
        if (!ParseFields(line+7,values,13) || values[0]<0 || values[0]>INT32_MAX || values[1]<0 || values[1]>2147483646 ||
            values[2]<0 || values[2]>3 || values[3]<0 || values[3]>6 || values[4]<0 || values[4]>100 ||
            values[5]<0 || values[5]>1 || values[6]<-8200 || values[6]>8200 || values[7]<-8200 || values[7]>8200 ||
            values[8]<-32768 || values[8]>32768 || values[9]<-32768 || values[9]>32768 ||
            values[10]<-3300000 || values[10]>3300000 || values[11]<-3300000 || values[11]>3300000 ||
            values[12]<1 || values[12]>2147483646) return;
        MarkFrame();
        std::lock_guard<std::mutex> lock(tuning_mutex_);
        if (!gyro_seen_ || values[2]!=gyro_fields_[2] || values[3]!=gyro_fields_[3] || values[1]!=gyro_fields_[1]) {
            ESP_LOGI(kTag, "Gyro calibration state=%d reason=%d progress=%d bias_x100=%d,%d raw=%d,%d",
                     static_cast<int>(values[2]), static_cast<int>(values[3]), static_cast<int>(values[4]),
                     static_cast<int>(values[6]), static_cast<int>(values[7]), static_cast<int>(values[8]), static_cast<int>(values[9]));
        }
        for (int i=0;i<13;++i) gyro_fields_[i]=values[i];
        gyro_seen_=true; gyro_tick_=now;
        return;
    }
    if (strncmp(line,"TSTATE,",7)==0) {
        int64_t values[12]{};
        if (!ParseFields(line+7,values,12) || values[0]<0 || values[0]>65535 || values[1]<0 || values[1]>6 ||
            values[2]<0 || values[2]>INT32_MAX || values[3]<1 || values[3]>2147483646 ||
            values[4]<0 || values[4]>1 || values[5]<0 || values[5]>1 || !TuningValuesValid(values+6)) return;
        MarkFrame();
        std::lock_guard<std::mutex> lock(tuning_mutex_);
        if (tuning_seen_ && ((static_cast<uint32_t>(values[2]-tuning_fields_[2]))&0x7FFFFFFFU)>0x3FFFFFFFU) {
            live_points_.clear(); live_cursor_=0; live_seen_=false;
            tuning_epoch_=tuning_epoch_>=INT32_MAX?1:tuning_epoch_+1;
        }
        for (int i=0;i<12;++i) tuning_fields_[i]=values[i];
        tuning_seen_=true; tuning_tick_=now;
        if (values[0] && tuning_pending_.load()==values[0]) {
            for (int i=0;i<12;++i) tuning_ack_fields_[i]=values[i];
            xSemaphoreGive(tuning_semaphore_);
        }
        return;
    }
    if (strncmp(line,"DIAG,LIVE,",10)==0) {
        std::array<int64_t,19> values{};
        if (!ParseFields(line+10,values.data(),values.size())) {
            if (!ParseFields(line+10,values.data(),17)) return;
            values[17]=values[4]*100; values[18]=values[5]*100;
        }
        if ( values[0]<0 || values[0]>INT32_MAX ||
            values[1]<1 || values[1]>2147483646 || values[2]<0 || values[2]>1 ||
            values[3]<-18000 || values[3]>18000 || values[11]<-2600 || values[11]>2600 ||
            values[12]<-2600 || values[12]>2600 || values[15]<0 || values[15]>3 || values[16]<0 || values[16]>63 ||
            values[17]<-3300000 || values[17]>3300000 || values[18]<-3300000 || values[18]>3300000) return;
        MarkFrame();
        std::lock_guard<std::mutex> lock(tuning_mutex_);
        if (!live_points_.empty() && live_points_.back().fields[0]==values[0]) return;
        if (!live_points_.empty() && ((static_cast<uint32_t>(values[0]-live_points_.back().fields[0]))&0x7FFFFFFFU)>0x3FFFFFFFU) {
            live_points_.clear(); live_cursor_=0;
            tuning_epoch_=tuning_epoch_>=INT32_MAX?1:tuning_epoch_+1;
        }
        if (live_cursor_>=INT32_MAX) { live_points_.clear();live_cursor_=0;tuning_epoch_=tuning_epoch_>=INT32_MAX?1:tuning_epoch_+1; }
        live_points_.push_back({++live_cursor_,values});
        if (live_points_.size()>120) live_points_.pop_front();
        live_tick_=now; live_seen_=true;
        return;
    }
    if (strncmp(line, "CALACK,", 7) == 0) {
        int64_t values[2]{};
        if (!ParseFields(line + 7, values, 2) || values[0] <= 0 || values[0] > 65535 || values[1] < 0 || values[1] > 8) return;
        MarkFrame();
        if (calibration_pending_.load() == values[0]) {
            calibration_reply_.store(static_cast<int>(values[1]));
            xSemaphoreGive(calibration_semaphore_);
        }
        return;
    }
    if (strncmp(line, "DIAG,", 5) == 0) {
        int64_t fields[15]{};
        const bool motion = strncmp(line, "DIAG,M,", 7) == 0;
        bool valid = false;
        if (motion) {
            // STM32 Chassis_FormatDiagnostics: time, phase, error,
            // error_phase, stopped, rest, stable, drift, degrees_x10.
            valid = ParseFields(line + 7, fields, 9) && fields[0] >= 0 &&
                fields[1] >= 0 && fields[1] <= 5 && fields[2] >= 0 && fields[2] <= 11 &&
                fields[3] >= 0 && fields[3] <= 5 && fields[4] >= 0 && fields[4] <= 1 &&
                fields[5] >= 0 && fields[5] <= 3 && fields[6] >= 0;
            if (valid) {
                std::lock_guard<std::mutex> lock(motion_mutex_);
                motion_phase_ = static_cast<int>(fields[1]);
                motion_error_ = static_cast<int>(fields[2]);
                motion_rest_ = static_cast<int>(fields[5]);
                motion_stopped_ = fields[4] == 1;
                motion_tick_ = now;
                motion_seen_ = true;
                // Phase 0 is merely "no turn in progress", never DONE.
            }
        } else if (strncmp(line, "DIAG,S,", 7) == 0) {
            valid = ParseFields(line + 7, fields, 12) && fields[0] >= 0 &&
                fields[1] >= 0 && fields[1] <= 1 && fields[2] >= 0 && fields[3] >= 0 && fields[4] >= 0;
        } else if (strncmp(line, "DIAG,CTRL,", 10) == 0) {
            // Enabled samples only: PI state, common drive range, wheel travel and reversals.
            valid = ParseFields(line + 10, fields, 14) && fields[0] >= 0 &&
                fields[1] >= 0 && fields[1] <= 1 && fields[2] >= 0 &&
                fields[8] <= fields[9] && fields[12] >= 0 && fields[13] >= 0;
        } else if (strncmp(line, "DIAG,CAL,", 9) == 0) {
            valid = ParseFields(line + 9, fields, 12) && fields[0] >= 0 &&
                fields[1] >= 0 && fields[1] <= 1 && fields[2] >= 0 && fields[2] <= 65535 &&
                fields[3] >= 0 && fields[3] <= 2 && fields[4] >= -2600 && fields[4] <= 2600 &&
                fields[7] >= 0 && fields[8] >= 0 && fields[9] >= 0 && fields[9] <= 8 &&
                fields[10] >= 0 && fields[10] <= 8000 && fields[11] >= 0;
            if (valid) {
                std::lock_guard<std::mutex> lock(calibration_mutex_);
                for (size_t i = 0; i < 12; ++i) calibration_fields_[i] = fields[i];
                calibration_supported_.store(true);
                calibration_active_.store(fields[1] == 1);
                calibration_session_.store(static_cast<unsigned int>(fields[2]));
                calibration_tick_.store(now);
            }
        } else if (strncmp(line, "DIAG,RISE,", 10) == 0) {
            valid = ParseFields(line + 10, fields, 7) && fields[0] >= 0 && fields[0] <= 4 &&
                fields[1] >= 0 && fields[1] <= 7 && fields[2] >= 0 && fields[2] <= 10000 &&
                fields[3] >= -18000 && fields[3] <= 18000 &&
                fields[4] >= -33000 && fields[4] <= 33000 &&
                fields[5] >= -1000000 && fields[5] <= 1000000 && fields[6] >= 0 && fields[6] <= 1000;
            if (valid && fields[0] == 4) balance_stopped_.store(1);
        } else if (strncmp(line, "DIAG,FW,", 8) == 0) {
            valid = ParseFields(line + 8, fields, 3) && fields[0] > 0 && fields[1] == 2 && fields[2] >= 0;
        } else if (strncmp(line, "DIAG,CFG,", 9) == 0) {
            valid = ParseFields(line + 9, fields, 11);
            for (int i = 1; valid && i < 11; ++i) valid = fields[i] >= 0;
            valid = valid && fields[9] > 0 && fields[10] > 0;
        } else if (strncmp(line, "DIAG,COMP,", 10) == 0) {
            // Measured start/run pairs and bounded friction-assist settings.
            valid = ParseFields(line + 10, fields, 15);
            for (int i = 0; valid && i < 15; ++i) valid = fields[i] > 0;
            for (int i = 0; valid && i < 8; i += 2) valid = fields[i + 1] <= fields[i] && fields[i] <= 2600;
        } else if (strncmp(line, "DIAG,OBS,", 9) == 0) {
            valid = ParseFields(line + 9, fields, 13);
            for (int i = 0; valid && i < 10; ++i) valid = fields[i] >= 0;
            valid = valid && fields[2] <= fields[1] && fields[5] <= fields[1] &&
                fields[6] <= fields[1] && fields[7] <= fields[1] &&
                fields[8] <= fields[9] && fields[11] <= fields[12];
        } else if (strncmp(line, "DIAG,BT,", 8) == 0) {
            valid = ParseFields(line + 8, fields, 1) && fields[0] >= 0 && fields[0] <= 1;
            if (valid) { bluetooth_mode_.store(static_cast<int>(fields[0])); bluetooth_tick_.store(now); }
        } else if (strncmp(line, "DIAG,STABLE,", 12) == 0) {
            valid = ParseFields(line + 12, fields, 14);
            for (int i = 0; valid && i < 14; ++i) valid = fields[i] >= 0;
            valid = valid && fields[0] <= 65535 && fields[1] <= 5 && fields[4] <= 63;
        } else if (strncmp(line, "DIAG,TURN_REQ,", 14) == 0) {
            valid = ParseFields(line + 14, fields, 2) && fields[0] > 0 && fields[0] <= 65535 &&
                fields[1] >= 1 && fields[1] <= 3;
            // A receipt proves parsing only; it is not an ACK/DONE substitute.
        } else if (strncmp(line, "DIAG,TURN_DT,", 13) == 0) {
            valid = ParseFields(line + 13, fields, 5);
            for (int i = 0; valid && i < 5; ++i) valid = fields[i] >= 0;
            valid = valid && fields[0] <= 65535;
        } else if (strncmp(line, "DIAG,LINK,COUNTS,", 17) == 0) {
            valid = ParseFields(line + 17, fields, 8);
            for (int i = 0; valid && i < 8; ++i) valid = fields[i] >= 0;
        } else if (strncmp(line, "DIAG,BAT,", 9) == 0) {
            valid = ParseFields(line + 9, fields, 2) && fields[0] >= 0 && fields[0] <= 1 && fields[1] >= 0;
            // This diagnostic is emitted only when Battery_IsReady fails.
            if (valid) status_valid_.store(false);
        }
        if (valid) {
            MarkFrame(); // Diagnostic heartbeat never refreshes battery TTL.
            // Keep parsing posture telemetry for the web panel and action checks,
            // but do not flood the ESP32 serial console with periodic samples.
            const bool posture_telemetry = motion || strncmp(line, "DIAG,S,", 7) == 0 ||
                strncmp(line, "DIAG,CTRL,", 10) == 0 || strncmp(line, "DIAG,OBS,", 9) == 0 ||
                strncmp(line, "DIAG,STABLE,", 12) == 0;
            if (!posture_telemetry) ESP_LOGI(kTag, "STM32 diagnostic: %s", line);
        } else ESP_LOGW(kTag, "Unknown/malformed diagnostic (no heartbeat): %s", line);
        return;
    }
    if (strcmp(line, "FAULT,IMU_TIMEOUT") == 0 || strcmp(line, "FAULT,STAND_FAILED") == 0) {
        balance_stopped_.store(1);
        MarkFrame();
        ESP_LOGE(kTag, "STM32 stopped motors: %s", line);
        return;
    }
    unsigned int reply_sequence = 0;
    int reply = kNoReply;
    if (sscanf(line, "ACK,%u%c", &reply_sequence, &extra) == 1) {
        reply = kAccepted;
    } else {
        char error[24] = {};
        if (sscanf(line, "ERR,%u,%23[A-Z_]%c", &reply_sequence, error, &extra) == 2) {
            ESP_LOGW(kTag, "STM32 request %u rejected: %s", reply_sequence, error);
            if (strcmp(error, "NOT_READY") == 0) reply = kNotReady;
            else if (strcmp(error, "LOW_BATT") == 0) reply = kLowBattery;
            else if (strcmp(error, "ANGLE") == 0) reply = kAngle;
            else if (strcmp(error, "NOT_STANDING") == 0) reply = kNotStanding;
            else if (strcmp(error, "FAULT") == 0) reply = kFault;
            else if (strcmp(error, "BUSY") == 0) reply = kBusy;
            else if (strcmp(error, "BT_ACTIVE") == 0) reply = kBluetoothActive;
            else reply = kUnknownError;
            ESP_LOGW(kTag, "STM32 rejected action: id=%u pending=%u kind=%d reason=%s", reply_sequence,
                static_cast<unsigned int>(pending_sequence_.load()), pending_kind_.load(), error);
        }
    }
    if (reply != kNoReply && reply_sequence && reply_sequence <= 65535) {
        if (reply == kAccepted && reply_sequence == pending_sequence_.load() && pending_reply_.load() == kNoReply)
            ESP_LOGI(kTag, "STM32 ACK action: id=%u kind=%d", reply_sequence, pending_kind_.load());
        MarkFrame();
        PublishReply(reply_sequence, 0, reply);
        return;
    }
    if (strcmp(line, "PONG,1") == 0) {
        MarkFrame();
        return;
    }
    int version = 0, millivolts = -1, stopped = 1, low = 0;
    unsigned int sequence = 0;
    if (sscanf(line, "BAT,%d,%u,%d,%d,%d%c", &version, &sequence,
               &millivolts, &stopped, &low, &extra) != 5 || version != 1 ||
        sequence > 65535 || millivolts < 0 || millivolts > 20000 ||
        (stopped != 0 && stopped != 1) || (low != 0 && low != 1)) return;
    if (millivolts == 0) {
        MarkFrame();
        status_valid_.store(false);
        return;
    }
    const int previous_mv = battery_mv_.exchange(millivolts);
    balance_stopped_.store(stopped);
    low_battery_.store(low);
    MarkFrame();
    last_battery_tick_ = now;
    status_valid_.store(true);
    if (previous_mv < 0 || sequence % 10 == 0) ESP_LOGI(kTag, "STM32 battery: %d mV (frame %u)", millivolts, sequence);
}
