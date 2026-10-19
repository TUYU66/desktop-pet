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

std::string Stm32Link::RequestStand() {
    return RequestMotion("STAND");
}

std::string Stm32Link::RequestBluetooth(bool enabled) {
    if (!enabled && started_.load() && uart_mutex_ && request_mutex_) {
        // Revoke first, including while an App rise owns the normal action slot.
        if (xSemaphoreTake(uart_mutex_, pdMS_TO_TICKS(1000)) == pdTRUE) {
            if (McpServer::GetInstance().CurrentCallIsValid()) WriteFrame("BT_REVOKE");
            xSemaphoreGive(uart_mutex_);
        }
        const auto deadline = xTaskGetTickCount() + pdMS_TO_TICKS(5000);
        while (static_cast<int32_t>(deadline - xTaskGetTickCount()) > 0) {
            if (xSemaphoreTake(request_mutex_, 0) == pdTRUE) {
                xSemaphoreGive(request_mutex_);
                if (!GetMotionSnapshot().busy) break;
            }
            vTaskDelay(pdMS_TO_TICKS(20));
        }
    }
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

std::string Stm32Link::RequestMotion(const char* command, unsigned int app_request) {
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
    char request[36];
    if (app_request) snprintf(request, sizeof(request), "%s,%u,%u", command, static_cast<unsigned int>(sequence), app_request);
    else snprintf(request, sizeof(request), "%s,%u", command, static_cast<unsigned int>(sequence));
    // Pending publication and retirement are serialized with the RX task.
    if (xSemaphoreTake(uart_mutex_, pdMS_TO_TICKS(1000)) != pdTRUE) {
        FinishAction("not_sent");
        return "我正在处理上一个动作，稍等一下。";
    }
    if (!McpServer::GetInstance().CurrentCallIsValid() || !LinkFresh() ||
        (app_request && app_bluetooth_id_.load() != app_request)) {
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
