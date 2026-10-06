#include "protocol.h"

#include <esp_log.h>

#define TAG "Protocol"

void Protocol::SendStandbyAck(const std::string& id) {
    auto root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "listen");
    cJSON_AddStringToObject(root, "state", "standby");
    cJSON_AddStringToObject(root, "requestId", id.c_str());
    char* json = cJSON_PrintUnformatted(root);
    if (json) { SendText(json); cJSON_free(json); }
    cJSON_Delete(root);
}

void Protocol::OnIncomingJson(std::function<void(const cJSON* root)> callback) {
    on_incoming_json_ = callback;
}

void Protocol::OnIncomingAudio(std::function<void(std::unique_ptr<AudioStreamPacket> packet)> callback) {
    on_incoming_audio_ = callback;
}

void Protocol::OnAudioChannelOpened(std::function<void()> callback) {
    on_audio_channel_opened_ = callback;
}

void Protocol::OnAudioChannelClosed(std::function<void()> callback) {
    on_audio_channel_closed_ = callback;
}

void Protocol::OnNetworkError(std::function<void(const std::string& message)> callback) {
    on_network_error_ = callback;
}

void Protocol::OnConnected(std::function<void()> callback) {
    on_connected_ = callback;
}

void Protocol::OnDisconnected(std::function<void()> callback) {
    on_disconnected_ = callback;
}

void Protocol::SetError(const std::string& message) {
    // error_occurred_ 会让 IsAudioChannelOpened() 立即返回 false，避免继续向坏连接发送数据。
    error_occurred_ = true;
    if (on_network_error_ != nullptr) {
        on_network_error_(message);
    }
}

void Protocol::SendAbortSpeaking(AbortReason reason) {
    // abort 用于停止当前服务端生成/播放；唤醒词打断会携带专用原因。
    std::string message = "{\"session_id\":\"" + session_id_ + "\",\"type\":\"abort\"";
    if (reason == kAbortReasonWakeWordDetected) {
        message += ",\"reason\":\"wake_word_detected\"";
    } else if (reason == kAbortReasonSpeechDetected) {
        message += ",\"reason\":\"speech_detected\"";
    }
    message += "}";
    SendText(message);
}

void Protocol::SendWakeWordDetected(const std::string& wake_word) {
    // 唤醒词先作为控制消息发送，服务器随后决定是否进入正式监听。
    std::string json = "{\"session_id\":\"" + session_id_ + 
                      "\",\"type\":\"listen\",\"state\":\"detect\",\"text\":\"" + wake_word + "\"}";
    SendText(json);
}

void Protocol::SendStartListening(ListeningMode mode) {
    // mode 告诉服务端本轮由 VAD 自动结束、手动结束，还是实时双工。
    std::string message = "{\"session_id\":\"" + session_id_ + "\"";
    message += ",\"type\":\"listen\",\"state\":\"start\"";
    if (mode == kListeningModeRealtime) {
        message += ",\"mode\":\"realtime\"";
    } else if (mode == kListeningModeAutoStop) {
        message += ",\"mode\":\"auto\"";
    } else {
        message += ",\"mode\":\"manual\"";
    }
    message += "}";
    SendText(message);
}

void Protocol::SendStopListening() {
    // 只结束上传侧的监听，不等于断开底层 WebSocket/MQTT。
    std::string message = "{\"session_id\":\"" + session_id_ + "\",\"type\":\"listen\",\"state\":\"stop\"}";
    SendText(message);
}

void Protocol::SendMcpMessage(const std::string& payload) {
    // payload 已是 MCP JSON 内容，这里只补协议外层信封。
    std::string message = "{\"session_id\":\"" + session_id_ + "\",\"type\":\"mcp\",\"payload\":" + payload + "}";
    SendText(message);
}

bool Protocol::IsTimeout() const {
    // 使用最近一次服务端入站数据判断死连接；本地发送并不能证明服务端仍在线。
    const int kTimeoutSeconds = 120;
    auto now = std::chrono::steady_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::seconds>(now - last_incoming_time_);
    bool timeout = duration.count() > kTimeoutSeconds;
    if (timeout) {
        ESP_LOGE(TAG, "Channel timeout %ld seconds", (long)duration.count());
    }
    return timeout;
}

void Protocol::SendWakeWordConfigResult(const std::string& id, const std::string& word, const std::string& status) {
    auto root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "wake_word_config_result");
    cJSON_AddStringToObject(root, "requestId", id.c_str());
    cJSON_AddStringToObject(root, "word", word.c_str());
    cJSON_AddStringToObject(root, "status", status.c_str());
    char* json = cJSON_PrintUnformatted(root);
    if (json) { SendText(json); cJSON_free(json); }
    cJSON_Delete(root);
}
