#ifndef _WEBSOCKET_PROTOCOL_H_
#define _WEBSOCKET_PROTOCOL_H_


#include "protocol.h"
#include <atomic>
#include <mutex>

#include <web_socket.h>
#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>

#define WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT (1 << 0)

class WebsocketProtocol : public Protocol {
public:
    WebsocketProtocol();
    ~WebsocketProtocol();

    bool Start() override;
    bool SendAudio(std::unique_ptr<AudioStreamPacket> packet) override;
    bool OpenAudioChannel() override;
    void CloseAudioChannel(bool send_goodbye = true) override;
    bool IsAudioChannelOpened() const override;
    ManagementServiceState GetManagementServiceState() const override;
    void EnterStandby() override;
    void MaintainConnection(bool allow_reconnect) override;

private:
    EventGroupHandle_t event_group_handle_;  // OpenAudioChannel 等待 server hello 的同步信号。
    // A cancelled background connect retains its socket until it returns.
    // The mutex protects ownership only, never a blocking network request.
    std::shared_ptr<WebSocket> websocket_;
    mutable std::mutex socket_mutex_;
    std::shared_ptr<WebSocket> GetSocket() const;
    std::atomic<uint32_t> connection_generation_{0};
    std::atomic<bool> opening_{false};
    std::atomic<uint32_t> ready_generation_{0};
    std::atomic<TickType_t> last_receive_tick_{0};
    std::atomic<TickType_t> last_pong_tick_{0};
    std::atomic<TickType_t> management_tick_{0};
    std::atomic<ManagementServiceState> management_state_{ManagementServiceState::Unknown};
    std::atomic<bool> service_status_supported_{false};
    int version_ = 1;  // 1=裸 Opus，2/3=带二进制包头。
    std::atomic<bool> standby_supported_{false};  // 只有双方握手确认后才允许待命保活。
    std::atomic<bool> standby_connecting_{false};  // 后台待命重连时抑制面向用户的错误弹窗。

    void ParseServerHello(const cJSON* root, uint32_t generation);
    void ParseServiceStatus(const cJSON* root);
    bool IsTimeout() const override;
    bool SendText(const std::string& text) override;
    std::string GetHelloMessage();
};

#endif
