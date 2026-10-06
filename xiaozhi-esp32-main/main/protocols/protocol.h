#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <cJSON.h>
#include <string>
#include <functional>
#include <chrono>
#include <vector>
#include <atomic>
#include <memory>
#include <cstdint>

#include "device_readiness.h"

struct AudioStreamPacket {
    int sample_rate = 0;       // PCM 解码/播放所需采样率。
    int frame_duration = 0;    // 单个 Opus 包对应的音频时长（毫秒）。
    uint32_t timestamp = 0;    // 服务端 AEC 用时间戳；未使用时为 0。
    std::vector<uint8_t> payload;  // Opus 压缩数据，不是裸 PCM。
};

// WebSocket 二进制协议 v2：字段使用网络字节序，带服务端 AEC 时间戳。
struct BinaryProtocol2 {
    uint16_t version;
    uint16_t type;          // 消息类型：0 为 Opus，1 为 JSON。
    uint32_t reserved;      // 预留字段，当前发送时置 0。
    uint32_t timestamp;     // 毫秒时间戳，供服务端回声消除对齐音频。
    uint32_t payload_size;  // payload 的字节数。
    uint8_t payload[];      // 柔性数组，实际数据紧跟结构体头部。
} __attribute__((packed));

// WebSocket 二进制协议 v3：更短的包头，适合不需要时间戳的音频帧。
struct BinaryProtocol3 {
    uint8_t type;
    uint8_t reserved;
    uint16_t payload_size;
    uint8_t payload[];
} __attribute__((packed));

enum AbortReason {
    kAbortReasonNone,              // 普通停止。
    kAbortReasonWakeWordDetected,  // 播放过程中再次听到唤醒词，立即打断。
    kAbortReasonSpeechDetected    // 通过回声过滤和持续语音确认的说话打断。
};

enum ListeningMode {
    kListeningModeAutoStop,    // VAD 判断说话结束后自动停止。
    kListeningModeManualStop,  // 必须由按钮或上层显式停止。
    kListeningModeRealtime     // 边播放边监听，需要设备端或服务端 AEC。
};

class Protocol {
public:
    virtual ~Protocol() = default;

    inline int server_sample_rate() const {
        return server_sample_rate_;
    }
    inline int server_frame_duration() const {
        return server_frame_duration_;
    }
    inline const std::string& session_id() const {
        return session_id_;
    }

    void OnIncomingAudio(std::function<void(std::unique_ptr<AudioStreamPacket> packet)> callback);
    void OnIncomingJson(std::function<void(const cJSON* root)> callback);
    void OnAudioChannelOpened(std::function<void()> callback);
    void OnAudioChannelClosed(std::function<void()> callback);
    void OnNetworkError(std::function<void(const std::string& message)> callback);
    void OnConnected(std::function<void()> callback);
    void OnDisconnected(std::function<void()> callback);

    // Protocol 只定义业务语义，WebSocket/MQTT 子类负责具体传输。
    virtual bool Start() = 0;
    virtual bool OpenAudioChannel() = 0;
    virtual void CloseAudioChannel(bool send_goodbye = true) = 0;
    virtual bool IsAudioChannelOpened() const = 0;
    virtual ManagementServiceState GetManagementServiceState() const { return ManagementServiceState::Unknown; }
    virtual bool SendAudio(std::unique_ptr<AudioStreamPacket> packet) = 0;
    // 默认协议待命时关闭连接；WebSocket 可覆写为保留传输连接。
    virtual void EnterStandby() { CloseAudioChannel(); }
    // 主循环周期调用；支持常连接的协议可在这里发心跳或尝试重连。
    virtual void MaintainConnection(bool allow_reconnect) { (void)allow_reconnect; }
    void SendWakeWordConfigResult(const std::string& id, const std::string& word, const std::string& status);
    void SendStandbyAck(const std::string& id);
    virtual void SendWakeWordDetected(const std::string& wake_word);
    virtual void SendStartListening(ListeningMode mode);
    virtual void SendStopListening();
    virtual void SendAbortSpeaking(AbortReason reason);
    virtual void SendMcpMessage(const std::string& message);

protected:
    std::function<void(const cJSON* root)> on_incoming_json_;
    std::function<void(std::unique_ptr<AudioStreamPacket> packet)> on_incoming_audio_;
    std::function<void()> on_audio_channel_opened_;
    std::function<void()> on_audio_channel_closed_;
    std::function<void(const std::string& message)> on_network_error_;
    std::function<void()> on_connected_;
    std::function<void()> on_disconnected_;

    int server_sample_rate_ = 24000;      // 握手后以服务器 hello 返回值为准。
    int server_frame_duration_ = 60;
    std::atomic<bool> error_occurred_{false};
    std::string session_id_;
    std::chrono::time_point<std::chrono::steady_clock> last_incoming_time_;  // 只用单调时钟计算超时。

    virtual bool SendText(const std::string& text) = 0;
    virtual void SetError(const std::string& message);
    virtual bool IsTimeout() const;
};

#endif // PROTOCOL_H
