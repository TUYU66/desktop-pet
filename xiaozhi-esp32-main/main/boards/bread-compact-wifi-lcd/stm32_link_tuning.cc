#include "stm32_link.h"
#include "config.h"
#include "application.h"
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

using namespace stm32_link_detail;

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
