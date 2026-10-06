#include "websocket_protocol.h"
#include "board.h"
#include "system_info.h"
#include "application.h"
#include "settings.h"
#include "mcp_server.h"

#include <cstring>
#include <cJSON.h>
#include <esp_log.h>
#include <arpa/inet.h>
#include "assets/lang_config.h"

#define TAG "WS"

WebsocketProtocol::WebsocketProtocol() {
    event_group_handle_ = xEventGroupCreate();
}

WebsocketProtocol::~WebsocketProtocol() {
    CloseAudioChannel();
    vEventGroupDelete(event_group_handle_);
}

bool WebsocketProtocol::Start() {
    // Application starts a background handshake once activation completes.
    return true;
}

bool WebsocketProtocol::SendAudio(std::unique_ptr<AudioStreamPacket> packet) {
    auto socket = GetSocket();
    if (!IsAudioChannelOpened() || !socket || !socket->IsConnected()) {
        return false;
    }

    if (version_ == 2) {
        // v2 包头含时间戳，所有多字节字段都要转为网络字节序。
        std::string serialized;
        serialized.resize(sizeof(BinaryProtocol2) + packet->payload.size());
        auto bp2 = (BinaryProtocol2*)serialized.data();
        bp2->version = htons(version_);
        bp2->type = 0;
        bp2->reserved = 0;
        bp2->timestamp = htonl(packet->timestamp);
        bp2->payload_size = htonl(packet->payload.size());
        memcpy(bp2->payload, packet->payload.data(), packet->payload.size());

        return socket->Send(serialized.data(), serialized.size(), true);
    } else if (version_ == 3) {
        // v3 省略采样率和时间戳，参数已在 hello 阶段协商。
        std::string serialized;
        serialized.resize(sizeof(BinaryProtocol3) + packet->payload.size());
        auto bp3 = (BinaryProtocol3*)serialized.data();
        bp3->type = 0;
        bp3->reserved = 0;
        bp3->payload_size = htons(packet->payload.size());
        memcpy(bp3->payload, packet->payload.data(), packet->payload.size());

        return socket->Send(serialized.data(), serialized.size(), true);
    } else {
        return socket->Send(packet->payload.data(), packet->payload.size(), true);
    }
}

bool WebsocketProtocol::SendText(const std::string& text) {
    auto socket = GetSocket();
    if (!socket || !socket->IsConnected()) {
        return false;
    }

    if (!socket->Send(text)) {
        ESP_LOGE(TAG, "Failed to send text: %s", text.c_str());
        if (!standby_connecting_) SetError(Lang::Strings::SERVER_ERROR);
        return false;
    }

    return true;
}

std::shared_ptr<WebSocket> WebsocketProtocol::GetSocket() const {
    std::lock_guard<std::mutex> lock(socket_mutex_);
    return websocket_;
}

bool WebsocketProtocol::IsAudioChannelOpened() const {
    const auto generation = connection_generation_.load();
    return generation != 0 && ready_generation_.load() == generation && !error_occurred_.load() && !IsTimeout();
}

bool WebsocketProtocol::IsTimeout() const {
    const auto now = xTaskGetTickCount();
    const auto last = service_status_supported_.load() ? last_pong_tick_.load() : last_receive_tick_.load();
    const unsigned seconds = service_status_supported_.load() ? 15 : 120;
    return static_cast<TickType_t>(now - last) >= pdMS_TO_TICKS(seconds * 1000);
}

ManagementServiceState WebsocketProtocol::GetManagementServiceState() const {
    if (!IsAudioChannelOpened() || !service_status_supported_.load() ||
        static_cast<TickType_t>(xTaskGetTickCount() - management_tick_.load()) >= pdMS_TO_TICKS(15000)) {
        return ManagementServiceState::Unknown;
    }
    return management_state_.load();
}

void WebsocketProtocol::CloseAudioChannel(bool send_goodbye) {
    (void)send_goodbye;
    connection_generation_.fetch_add(1);
    ready_generation_ = 0;
    management_state_ = ManagementServiceState::Unknown;
    std::shared_ptr<WebSocket> old_socket;
    {
        std::lock_guard<std::mutex> lock(socket_mutex_);
        old_socket = std::move(websocket_);
    }
    // A background handshake holds its own snapshot, so cancellation never
    // waits on TCP connect. Old callbacks are excluded by generation.
    if (old_socket) {
        McpServer::GetInstance().InvalidateSession();
        if (on_audio_channel_closed_) on_audio_channel_closed_();
    }
}

void WebsocketProtocol::EnterStandby() {
    // 新服务端支持 standby 时只关闭“对话态”，保留 TCP/WebSocket 以接收网页主动消息。
    // 老服务端未声明能力时仍物理断开，保证向后兼容。
    if (standby_supported_ && IsAudioChannelOpened()) {
        SendText(R"({"type":"listen","state":"standby"})");
        // The transport remains open. Do not queue a channel-closed callback:
        // it can arrive after a new music start and force the device idle.
    } else {
        CloseAudioChannel();
    }
}

void WebsocketProtocol::MaintainConnection(bool allow_reconnect) {
    (void)allow_reconnect; // Application owns serialized background retries.
    if (ready_generation_.load() != 0 && !IsAudioChannelOpened()) {
        ESP_LOGW(TAG, "Server heartbeat expired; close stale session");
        CloseAudioChannel();
    } else if (IsAudioChannelOpened() && (standby_supported_.load() || service_status_supported_.load())) {
        SendText(R"({"type":"ping"})");
    }
}

bool WebsocketProtocol::OpenAudioChannel() {
    if (opening_.exchange(true)) return false;
    struct OpeningGuard {
        std::atomic<bool>& flag;
        ~OpeningGuard() { flag = false; }
    } opening_guard{opening_};
    standby_connecting_ = true;
    // Invalidate before replacing a socket. Old callbacks and queued MCP
    // results cannot cross into the next connection, even if RPC ids repeat.
    CloseAudioChannel();
    McpServer::GetInstance().InvalidateSession(); // Separate queued close UI from this opening.
    const uint32_t generation = connection_generation_.load();
    const uint32_t mcp_session = McpServer::GetInstance().Session();
    // 每次握手都重新协商待命能力，不能沿用上一次连接的结果。
    xEventGroupClearBits(event_group_handle_, WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT);
    standby_supported_ = false;
    service_status_supported_ = false;
    Settings settings("websocket", false);
    std::string url = settings.GetString("url");
    std::string token = settings.GetString("token");
    int version = settings.GetInt("version");
    if (version != 0) {
        version_ = version;
    }

    error_occurred_ = false;
    if (url.empty()) {
        ESP_LOGW(TAG, "Websocket address is not configured");
        return false;
    }

    auto network = Board::GetInstance().GetNetwork();
    std::shared_ptr<WebSocket> socket = network->CreateWebSocket(1);
    if (!socket) {
        ESP_LOGE(TAG, "Failed to create websocket");
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(socket_mutex_);
        if (generation != connection_generation_.load()) return false;
        websocket_ = socket;
    }
    if (!token.empty()) {
        // 配置只写裸 token 时自动补 Bearer；已有认证方案前缀则原样使用。
        if (token.find(" ") == std::string::npos) {
            token = "Bearer " + token;
        }
        socket->SetHeader("Authorization", token.c_str());
    }
    socket->SetHeader("Protocol-Version", std::to_string(version_).c_str());
    socket->SetHeader("Device-Id", SystemInfo::GetMacAddress().c_str());
    socket->SetHeader("Client-Id", Board::GetInstance().GetUuid().c_str());

    socket->OnData([this, generation, mcp_session](const char* data, size_t len, bool binary) {
        if (generation != connection_generation_.load()) return;
        if (binary) {
            // 二进制帧是下行 Opus；按握手版本拆包后交给 AudioService 解码。
            ESP_LOGD(TAG, "Binary data received: len=%u, version=%d", static_cast<unsigned>(len), version_);
            if (on_incoming_audio_ != nullptr) {
                if (version_ == 2) {
                    BinaryProtocol2* bp2 = (BinaryProtocol2*)data;
                    bp2->version = ntohs(bp2->version);
                    bp2->type = ntohs(bp2->type);
                    bp2->timestamp = ntohl(bp2->timestamp);
                    bp2->payload_size = ntohl(bp2->payload_size);
                    auto payload = (uint8_t*)bp2->payload;
                    on_incoming_audio_(std::make_unique<AudioStreamPacket>(AudioStreamPacket{
                        .sample_rate = server_sample_rate_,
                        .frame_duration = server_frame_duration_,
                        .timestamp = bp2->timestamp,
                        .payload = std::vector<uint8_t>(payload, payload + bp2->payload_size)
                    }));
                } else if (version_ == 3) {
                    BinaryProtocol3* bp3 = (BinaryProtocol3*)data;
                    bp3->type = bp3->type;
                    bp3->payload_size = ntohs(bp3->payload_size);
                    auto payload = (uint8_t*)bp3->payload;
                    on_incoming_audio_(std::make_unique<AudioStreamPacket>(AudioStreamPacket{
                        .sample_rate = server_sample_rate_,
                        .frame_duration = server_frame_duration_,
                        .timestamp = 0,
                        .payload = std::vector<uint8_t>(payload, payload + bp3->payload_size)
                    }));
                } else {
                    on_incoming_audio_(std::make_unique<AudioStreamPacket>(AudioStreamPacket{
                        .sample_rate = server_sample_rate_,
                        .frame_duration = server_frame_duration_,
                        .timestamp = 0,
                        .payload = std::vector<uint8_t>((uint8_t*)data, (uint8_t*)data + len)
                    }));
                }
            }
        } else {
            // 文本帧是 hello、listen、tts、mcp 等控制消息。
            auto root = cJSON_ParseWithLength(data, len);
            auto type = cJSON_GetObjectItem(root, "type");
            if (cJSON_IsString(type)) {
                if (strcmp(type->valuestring, "hello") == 0) {
                    ParseServerHello(root, generation);
                } else if (strcmp(type->valuestring, "mcp") == 0) {
                    auto payload = cJSON_GetObjectItem(root, "payload");
                    if (cJSON_IsObject(payload)) McpServer::GetInstance().ReceiveMessage(payload, mcp_session);
                } else {
                    if (strcmp(type->valuestring, "pong") == 0) {
                        last_pong_tick_ = xTaskGetTickCount();
                        ParseServiceStatus(root);
                    }
                    if (on_incoming_json_ != nullptr) {
                        on_incoming_json_(root);
                    }
                }
            } else {
                ESP_LOGE(TAG, "Missing message type, data: %s", std::string(data, len).c_str());
            }
            cJSON_Delete(root);
        }
        last_receive_tick_ = xTaskGetTickCount();
    });

    socket->OnDisconnected([this, generation]() {
        uint32_t expected = generation;
        if (!connection_generation_.compare_exchange_strong(expected, generation + 1)) return;
        ready_generation_ = 0;
        management_state_ = ManagementServiceState::Unknown;
        McpServer::GetInstance().InvalidateSession();
        ESP_LOGI(TAG, "Websocket disconnected");
        if (on_audio_channel_closed_ != nullptr) {
            on_audio_channel_closed_();
        }
    });

    ESP_LOGI(TAG, "Connecting to websocket server: %s with version: %d", url.c_str(), version_);
    if (!socket->Connect(url.c_str())) {
        ESP_LOGE(TAG, "Failed to connect to websocket server, code=%d", socket->GetLastError());
        if (!standby_connecting_) SetError(Lang::Strings::SERVER_NOT_CONNECTED);
        if (generation == connection_generation_.load()) CloseAudioChannel();
        return false;
    }

    if (generation != connection_generation_.load()) return false;

    // 先发送 client hello，声明协议版本、音频参数和可选能力。
    auto message = GetHelloMessage();
    if (!SendText(message)) {
        if (generation == connection_generation_.load()) CloseAudioChannel();
        return false;
    }

    // The handshake runs off the main task. Allow cold server initialization
    // to finish without freezing display, VAD or hardware control.
    EventBits_t bits = xEventGroupWaitBits(event_group_handle_, WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT, pdTRUE, pdFALSE, pdMS_TO_TICKS(10000));
    if (!(bits & WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT)) {
        ESP_LOGE(TAG, "Failed to receive server hello");
        if (!standby_connecting_) SetError(Lang::Strings::SERVER_TIMEOUT);
        if (generation == connection_generation_.load()) CloseAudioChannel();
        return false;
    }

    if (generation != connection_generation_.load() || !socket->IsConnected() || !IsAudioChannelOpened()) return false;
    standby_connecting_ = false;
    if (on_audio_channel_opened_ != nullptr) {
        on_audio_channel_opened_();
    }

    return true;
}

std::string WebsocketProtocol::GetHelloMessage() {
    // hello 是能力协商，不是开始录音；真正监听还需后续 listen:start。
    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "hello");
    cJSON_AddNumberToObject(root, "version", version_);
    cJSON* features = cJSON_CreateObject();
#if CONFIG_USE_DEVICE_AEC
    cJSON_AddBoolToObject(features, "voice_barge_in",
        Application::GetInstance().GetAecMode() == kAecOnDeviceSide);
    cJSON_AddBoolToObject(features, "voice_barge_in_gated",
        Application::GetInstance().GetAecMode() == kAecOnDeviceSide);
#endif
#if CONFIG_USE_SERVER_AEC
    cJSON_AddBoolToObject(features, "aec", true);
#endif
    cJSON_AddBoolToObject(features, "asr_status", true);
    cJSON_AddBoolToObject(features, "service_status", true);
    cJSON_AddBoolToObject(features, "mcp", true);
#ifdef CONFIG_USE_DYNAMIC_WAKE_WORD
    cJSON_AddBoolToObject(features, "dynamic_wake_word", true);
#endif
#if CONFIG_WEBSOCKET_STANDBY_CONNECTION
    cJSON_AddBoolToObject(features, "standby_connection", true);
    cJSON_AddBoolToObject(features, "standby_ack", true);
#endif
    cJSON_AddItemToObject(root, "features", features);
    cJSON_AddStringToObject(root, "transport", "websocket");
    cJSON* audio_params = cJSON_CreateObject();
    cJSON_AddStringToObject(audio_params, "format", "opus");
    cJSON_AddNumberToObject(audio_params, "sample_rate", 16000);
    cJSON_AddNumberToObject(audio_params, "channels", 1);
    cJSON_AddNumberToObject(audio_params, "frame_duration", OPUS_FRAME_DURATION_MS);
    cJSON_AddItemToObject(root, "audio_params", audio_params);
    auto json_str = cJSON_PrintUnformatted(root);
    std::string message(json_str);
    cJSON_free(json_str);
    cJSON_Delete(root);
    return message;
}

void WebsocketProtocol::ParseServerHello(const cJSON* root, uint32_t generation) {
    if (generation != connection_generation_.load()) return;
    // 服务端必须显式回传 standby_connection=true，设备才会保留待命连接。
    auto features = cJSON_GetObjectItem(root, "features");
    standby_supported_ = cJSON_IsTrue(cJSON_GetObjectItem(features, "standby_connection"));
    service_status_supported_ = cJSON_IsTrue(cJSON_GetObjectItem(features, "service_status"));
    auto transport = cJSON_GetObjectItem(root, "transport");
    if (!cJSON_IsString(transport) || strcmp(transport->valuestring, "websocket") != 0) {
        ESP_LOGE(TAG, "Unsupported or missing transport");
        return;
    }

    auto session_id = cJSON_GetObjectItem(root, "session_id");
    if (cJSON_IsString(session_id)) {
        session_id_ = session_id->valuestring;
        ESP_LOGI(TAG, "Session ID: %s", session_id_.c_str());
    }

    auto audio_params = cJSON_GetObjectItem(root, "audio_params");
    if (cJSON_IsObject(audio_params)) {
        auto sample_rate = cJSON_GetObjectItem(audio_params, "sample_rate");
        if (cJSON_IsNumber(sample_rate)) {
            server_sample_rate_ = sample_rate->valueint;
        }
        auto frame_duration = cJSON_GetObjectItem(audio_params, "frame_duration");
        if (cJSON_IsNumber(frame_duration)) {
            server_frame_duration_ = frame_duration->valueint;
        }
    }

    last_receive_tick_ = xTaskGetTickCount();
    last_pong_tick_ = last_receive_tick_.load();
    ParseServiceStatus(root);
    // MCP initialization can arrive immediately after hello while the main
    // task is already running. Its replies must be allowed before the worker
    // wakes from the hello event.
    ready_generation_ = generation; // A concurrent close invalidates this generation even if this store arrives late.
    if (generation != connection_generation_.load()) return;
    xEventGroupSetBits(event_group_handle_, WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT);
}

void WebsocketProtocol::ParseServiceStatus(const cJSON* root) {
    if (!service_status_supported_.load()) return;
    auto services = cJSON_GetObjectItem(root, "services");
    auto java = cJSON_GetObjectItem(services, "java");
    ManagementServiceState state = ManagementServiceState::Unknown;
    if (cJSON_IsString(java)) {
        if (strcmp(java->valuestring, "online") == 0) state = ManagementServiceState::Online;
        else if (strcmp(java->valuestring, "offline") == 0) state = ManagementServiceState::Offline;
        else if (strcmp(java->valuestring, "checking") == 0) state = ManagementServiceState::Checking;
    }
    management_state_ = state;
    management_tick_ = xTaskGetTickCount();
}
