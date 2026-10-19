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

using namespace stm32_link_detail;

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
    if (strcmp(line, "BT_REVOKED,0") == 0) {
        MarkFrame(); app_bluetooth_id_.store(0);
        InterruptBluetoothPrompt();
        return;
    }
    int64_t app_fields[2]{};
    if (strncmp(line, "APP_REQ,", 8) == 0 && ParseFields(line + 8, app_fields, 2) &&
        app_fields[0] >= 1 && app_fields[0] <= 65535 && app_fields[1] >= 0 && app_fields[1] <= 1) {
        MarkFrame();
        const auto identifier = static_cast<unsigned int>(app_fields[0]);
        if (app_bluetooth_id_.load() == identifier) return;
        app_bluetooth_id_.store(identifier); app_bluetooth_target_.store(app_fields[1] != 0);
        InterruptBluetoothPrompt();
        AppBluetoothRequest request{identifier, app_fields[1] != 0};
        if (app_bluetooth_queue_) xQueueOverwrite(app_bluetooth_queue_, &request);
        else {
            char failed[40]; snprintf(failed, sizeof(failed), "APP_RESULT,%u,0", identifier);
            WriteFrame(failed); // RX foreground already owns uart_mutex_.
        }
        return;
    }
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
