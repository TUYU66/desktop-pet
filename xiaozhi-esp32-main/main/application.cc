#include <sdkconfig.h>
#ifdef CONFIG_USE_DYNAMIC_WAKE_WORD
#include "audio/wake_words/dynamic_wake_word.h"
#endif
#include "application.h"
#include "board.h"
#include "display.h"
#include "system_info.h"
#include "audio_codec.h"
#include "websocket_protocol.h"
#include "assets/lang_config.h"
#include "mcp_server.h"
#include "assets.h"
#include "settings.h"

#include <cstring>
#include <esp_log.h>
#include <cJSON.h>
#include <driver/gpio.h>
#include <arpa/inet.h>
#include <font_awesome.h>

#define TAG "Application"

// Application 是固件业务中枢。网络、音频和按钮回调只投递事件，
// 主循环再按设备状态串行执行，避免多个 FreeRTOS 任务同时修改界面和对话状态。

Application::Application() {
    event_group_ = xEventGroupCreate();

#if CONFIG_USE_DEVICE_AEC && CONFIG_USE_SERVER_AEC
#error "CONFIG_USE_DEVICE_AEC and CONFIG_USE_SERVER_AEC cannot be enabled at the same time"
#elif CONFIG_USE_DEVICE_AEC
    aec_mode_ = kAecOnDeviceSide;
#elif CONFIG_USE_SERVER_AEC
    aec_mode_ = kAecOnServerSide;
#else
    aec_mode_ = kAecOff;
#endif

    esp_timer_create_args_t clock_timer_args = {
        .callback = [](void* arg) {
            Application* app = (Application*)arg;
            xEventGroupSetBits(app->event_group_, MAIN_EVENT_CLOCK_TICK);
        },
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "clock_timer",
        .skip_unhandled_events = true
    };
    esp_timer_create(&clock_timer_args, &clock_timer_handle_);
}

Application::~Application() {
    if (clock_timer_handle_ != nullptr) {
        esp_timer_stop(clock_timer_handle_);
        esp_timer_delete(clock_timer_handle_);
    }
    vEventGroupDelete(event_group_);
}

bool Application::SetDeviceState(DeviceState state) {
    return state_machine_.TransitionTo(state);
}

void Application::Initialize() {
    // 初始化顺序很重要：先有显示和音频，再注册回调，最后异步启动网络。
    auto& board = Board::GetInstance();
    SetDeviceState(kDeviceStateStarting);

    // 初始化显示，并先显示板型/固件信息，便于启动阶段排查配置是否正确。
    auto display = board.GetDisplay();
    display->SetupUI();
    // Print board name/version info
    display->SetChatMessage("system", SystemInfo::GetUserAgent().c_str());

    // AudioService 内部会创建采集、播放和 Opus 编解码任务。
    auto codec = board.GetAudioCodec();
    audio_service_.Initialize(codec);
    if (aec_mode_ == kAecOnDeviceSide && !audio_service_.HasDeviceAecReference()) {
        ESP_LOGW(TAG, "AEC reference unavailable; using half duplex");
        aec_mode_ = kAecOff;
    }
    audio_service_.Start();

    AudioServiceCallbacks callbacks;
    callbacks.on_send_queue_available = [this]() {
        xEventGroupSetBits(event_group_, MAIN_EVENT_SEND_AUDIO);
    };
    callbacks.on_wake_word_detected = [this](const std::string& wake_word) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_WAKE_WORD_DETECTED);
    };
    callbacks.on_vad_change = [this](bool speaking) {
        if (speaking && GetDeviceState() == kDeviceStateSpeaking) {
            speech_barge_in_epoch_.store(playback_epoch_.load());
            speech_barge_in_pending_.store(true);
        }
        xEventGroupSetBits(event_group_, MAIN_EVENT_VAD_CHANGE);
    };
    audio_service_.SetCallbacks(callbacks);

    // 状态监听器只置事件位，显示和音频开关由主循环统一更新。
    state_machine_.AddStateChangeListener([this](DeviceState old_state, DeviceState new_state) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_STATE_CHANGED);
    });

    // 1 秒时钟用于刷新状态栏、打印诊断信息和维护待命连接。
    esp_timer_start_periodic(clock_timer_handle_, 1000000);

    // 注册设备侧 MCP 工具，例如音量、灯光等；整个生命周期只注册一次。
    auto& mcp_server = McpServer::GetInstance();
    mcp_server.AddCommonTools();
    mcp_server.AddUserOnlyTools();

    // 网络组件可能运行在其他任务，因此这里只更新轻量状态或设置主事件位。
    board.SetNetworkEventCallback([this](NetworkEvent event, const std::string& data) {
        auto display = Board::GetInstance().GetDisplay();
        
        switch (event) {
            case NetworkEvent::Scanning:
                display->ShowNotification(Lang::Strings::SCANNING_WIFI, 30000);
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_DISCONNECTED);
                break;
            case NetworkEvent::Connecting: {
                if (data.empty()) {
                    // Cellular network - registering without carrier info yet
                    display->SetStatus(Lang::Strings::REGISTERING_NETWORK);
                } else {
                    // WiFi or cellular with carrier info
                    std::string msg = Lang::Strings::CONNECT_TO;
                    msg += data;
                    msg += "...";
                    display->ShowNotification(msg.c_str(), 30000);
                }
                break;
            }
            case NetworkEvent::Connected: {
                std::string msg = Lang::Strings::CONNECTED_TO;
                msg += data;
                display->ShowNotification(msg.c_str(), 30000);
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_CONNECTED);
                break;
            }
            case NetworkEvent::Disconnected:
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_DISCONNECTED);
                break;
            case NetworkEvent::WifiConfigModeEnter:
                // WiFi config mode enter is handled by WifiBoard internally
                break;
            case NetworkEvent::WifiConfigModeExit:
                // WiFi config mode exit is handled by WifiBoard internally
                break;
            // Cellular modem specific events
            case NetworkEvent::ModemDetecting:
                display->SetStatus(Lang::Strings::DETECTING_MODULE);
                break;
            case NetworkEvent::ModemErrorNoSim:
                Alert(Lang::Strings::ERROR, Lang::Strings::PIN_ERROR, "triangle_exclamation", Lang::Sounds::OGG_ERR_PIN);
                break;
            case NetworkEvent::ModemErrorRegDenied:
                Alert(Lang::Strings::ERROR, Lang::Strings::REG_ERROR, "triangle_exclamation", Lang::Sounds::OGG_ERR_REG);
                break;
            case NetworkEvent::ModemErrorInitFailed:
                Alert(Lang::Strings::ERROR, Lang::Strings::MODEM_INIT_ERROR, "triangle_exclamation", Lang::Sounds::OGG_EXCLAMATION);
                break;
            case NetworkEvent::ModemErrorTimeout:
                display->SetStatus(Lang::Strings::REGISTERING_NETWORK);
                break;
        }
    });

    // 异步联网，成功后由 MAIN_EVENT_NETWORK_CONNECTED 继续激活流程。
    board.StartNetwork();

    // Update the status bar immediately to show the network state
    display->UpdateStatusBar(true);
}

void Application::Run() {
    // 主任务需要及时搬运音频和处理控制事件，优先级设为 10。
    vTaskPrioritySet(nullptr, 10);

    const EventBits_t ALL_EVENTS = 
        MAIN_EVENT_SCHEDULE |
        MAIN_EVENT_SEND_AUDIO |
        MAIN_EVENT_WAKE_WORD_DETECTED |
        MAIN_EVENT_VAD_CHANGE |
        MAIN_EVENT_CLOCK_TICK |
        MAIN_EVENT_ERROR |
        MAIN_EVENT_NETWORK_CONNECTED |
        MAIN_EVENT_NETWORK_DISCONNECTED |
        MAIN_EVENT_TOGGLE_CHAT |
        MAIN_EVENT_START_LISTENING |
        MAIN_EVENT_STOP_LISTENING |
        MAIN_EVENT_ACTIVATION_DONE |
        MAIN_EVENT_STATE_CHANGED;

    while (true) {
        // pdTRUE 会在返回时清除已取出的事件位；一次可同时处理多个事件。
        auto bits = xEventGroupWaitBits(event_group_, ALL_EVENTS, pdTRUE, pdFALSE, portMAX_DELAY);

        if (bits & MAIN_EVENT_ERROR) {
            if (protocol_) protocol_->CloseAudioChannel();
            StopUnavailableConversation();
            ESP_LOGW(TAG, "Voice connection error: %s", last_error_message_.c_str());
            Board::GetInstance().GetDisplay()->SetChatMessage("system", last_error_message_.c_str());
            UpdateAvailability(true);
        }

        if (bits & MAIN_EVENT_NETWORK_CONNECTED) {
            HandleNetworkConnectedEvent();
        }

        if (bits & MAIN_EVENT_NETWORK_DISCONNECTED) {
            HandleNetworkDisconnectedEvent();
        }

        if (bits & MAIN_EVENT_ACTIVATION_DONE) {
            HandleActivationDoneEvent();
        }

        if (bits & MAIN_EVENT_STATE_CHANGED) {
            HandleStateChangedEvent();
        }

        if (bits & MAIN_EVENT_TOGGLE_CHAT) {
            HandleToggleChatEvent();
        }

        if (bits & MAIN_EVENT_START_LISTENING) {
            HandleStartListeningEvent();
        }

        if (bits & MAIN_EVENT_STOP_LISTENING) {
            HandleStopListeningEvent();
        }

        if (bits & MAIN_EVENT_VAD_CHANGE) {
            const bool speech_onset = speech_barge_in_pending_.exchange(false);
            if (speech_onset && speech_barge_in_epoch_.load() == playback_epoch_.load() &&
                    GetDeviceState() == kDeviceStateSpeaking && !music_playing_ &&
                    conversation_awake_.load() && speech_listen_allowed_ &&
                    listening_mode_ == kListeningModeRealtime) {
                ESP_LOGI(TAG, "Speech barge-in: stop playback and keep microphone running");
                AbortSpeaking(kAbortReasonSpeechDetected);
                SetListeningMode(kListeningModeRealtime);
            }
            if (GetDeviceState() == kDeviceStateListening) {
                if (audio_service_.IsVoiceDetected()) {
                    // A new utterance supersedes the previous processing/error hint.
                    listening_display_phase_ = ListeningDisplayPhase::Waiting;
                    listening_voice_hold_until_us_ = 0;
                } else if (listening_voice_active_ && listening_display_phase_ == ListeningDisplayPhase::Waiting) {
                    // A short pause is not proof that the server has started ASR.
                    listening_voice_hold_until_us_ = esp_timer_get_time() + 400000;
                }
                listening_voice_active_ = audio_service_.IsVoiceDetected();
                UpdateListeningStatus();
                auto led = Board::GetInstance().GetLed();
                led->OnStateChanged();
            }
        }

        if (bits & MAIN_EVENT_SEND_AUDIO) {
            // A continuously replenished send queue must not starve VAD/abort
            // events. Send a bounded batch, then give the main loop another turn.
            for (int sent = 0; sent < 4; ++sent) {
                auto packet = audio_service_.PopPacketFromSendQueue();
                if (!packet) break;
                if (protocol_ && !protocol_->SendAudio(std::move(packet))) break;
                if (sent == 3) xEventGroupSetBits(event_group_, MAIN_EVENT_SEND_AUDIO);
            }
        }

        if (bits & MAIN_EVENT_WAKE_WORD_DETECTED) {
            HandleWakeWordDetectedEvent();
        }

        if (bits & MAIN_EVENT_SCHEDULE) {
            std::unique_lock<std::mutex> lock(mutex_);
            auto tasks = std::move(main_tasks_);
            lock.unlock();
            for (auto& task : tasks) {
                task();
            }
        }

        if (bits & MAIN_EVENT_CLOCK_TICK) {
            clock_ticks_++;
            auto display = Board::GetInstance().GetDisplay();
            display->UpdateStatusBar();
            ++maintenance_ticks_;
            const auto state = GetDeviceState();
            if (protocol_ && (state == kDeviceStateIdle || state == kDeviceStateConnecting ||
                    state == kDeviceStateListening || state == kDeviceStateSpeaking)) {
                // A missed heartbeat ends even an active conversation. Sending
                // microphone data cannot prove that Python is still responding.
                if (maintenance_ticks_ % 5 == 0) protocol_->MaintainConnection(false);
                if (!protocol_->IsAudioChannelOpened() && !connection_task_running_ &&
                        (state == kDeviceStateListening || state == kDeviceStateSpeaking)) {
                    StopUnavailableConversation();
                }
                if (network_connected_ && maintenance_ticks_ % 5 == 0 &&
                        GetDeviceState() == kDeviceStateIdle && !protocol_->IsAudioChannelOpened()) {
                    StartConnectionTask();
                }
            }
            UpdateAvailability();
            if (GetDeviceState() == kDeviceStateListening && listening_voice_hold_until_us_ != 0 &&
                    esp_timer_get_time() >= listening_voice_hold_until_us_) {
                listening_voice_hold_until_us_ = 0;
                UpdateListeningStatus();
            }

            // Print debug info every 10 seconds
            if (clock_ticks_ % 10 == 0) {
                SystemInfo::PrintHeapStats();
            }
        }
    }
}

void Application::HandleNetworkConnectedEvent() {
    // 首次联网进入激活任务；已经运行中的断线重连只更新连接标记和状态栏。
    network_connected_ = true;
    ESP_LOGI(TAG, "Network connected");
    auto state = GetDeviceState();

    if (state == kDeviceStateStarting || state == kDeviceStateWifiConfiguring) {
        // Network is ready, start activation
        SetDeviceState(kDeviceStateActivating);
        if (activation_task_handle_ != nullptr) {
            ESP_LOGW(TAG, "Activation task already running");
            return;
        }

        xTaskCreate([](void* arg) {
            Application* app = static_cast<Application*>(arg);
            app->ActivationTask();
            app->activation_task_handle_ = nullptr;
            vTaskDelete(NULL);
        }, "activation", 4096 * 2, this, 2, &activation_task_handle_);
    }

    // Update the status bar immediately to show the network state
    auto display = Board::GetInstance().GetDisplay();
    display->UpdateStatusBar(true);
}

void Application::HandleNetworkDisconnectedEvent() {
    // 物理网络断开后立即关闭会话，防止上层误认为旧 WebSocket 仍可发送。
    network_connected_ = false;
    StopUnavailableConversation();
    // Close current conversation when network disconnected
    auto state = GetDeviceState();
    if (protocol_ && (state == kDeviceStateIdle || state == kDeviceStateConnecting || state == kDeviceStateListening || state == kDeviceStateSpeaking)) {
        ESP_LOGI(TAG, "Closing audio channel due to network disconnection");
        protocol_->CloseAudioChannel();
    }

    // Update the status bar immediately to show the network state
    auto display = Board::GetInstance().GetDisplay();
    display->UpdateStatusBar(true);
}

void Application::HandleActivationDoneEvent() {
    // Activation completion is not service/chassis readiness.
    ESP_LOGI(TAG, "Activation done");

    SystemInfo::PrintHeapStats();
    // Protocol ownership and generation belong to the main task. Only the
    // OTA network work runs in ActivationTask.
    InitializeProtocol();
    SetDeviceState(kDeviceStateIdle);

    has_server_time_ = ota_->HasServerTime();

    auto display = Board::GetInstance().GetDisplay();
    std::string message = std::string(Lang::Strings::VERSION) + ota_->GetCurrentVersion();
    display->ShowNotification(message.c_str());
    display->SetChatMessage("system", "");

    // Release OTA object after activation is complete
    ota_.reset();
    auto& board = Board::GetInstance();
    board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);

    StartConnectionTask();
    UpdateAvailability(true);

}

void Application::ActivationTask() {
    // 网络请求可能阻塞，激活流程在独立 FreeRTOS 任务中执行。
    ota_ = std::make_unique<Ota>();

    // Check for new assets version
    CheckAssetsVersion();

    // Check for new firmware version
    CheckNewVersion();

    // Protocol setup is performed on the main task after this event.
    // Signal completion to main loop
    xEventGroupSetBits(event_group_, MAIN_EVENT_ACTIVATION_DONE);
}

void Application::CheckAssetsVersion() {
    // 资源分区保存字体、提示音等内容，与应用固件 OTA 分开升级。
    // Only allow CheckAssetsVersion to be called once
    if (assets_version_checked_) {
        return;
    }
    assets_version_checked_ = true;

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto& assets = Assets::GetInstance();

    if (!assets.partition_valid()) {
        ESP_LOGW(TAG, "Assets partition is disabled for board %s", BOARD_NAME);
        return;
    }
    
    Settings settings("assets", true);
    // Check if there is a new assets need to be downloaded
    std::string download_url = settings.GetString("download_url");

    if (!download_url.empty()) {
        settings.EraseKey("download_url");

        char message[256];
        snprintf(message, sizeof(message), Lang::Strings::FOUND_NEW_ASSETS, download_url.c_str());
        Alert(Lang::Strings::LOADING_ASSETS, message, "cloud_arrow_down", Lang::Sounds::OGG_UPGRADE);
        
        // Wait for the audio service to be idle for 3 seconds
        vTaskDelay(pdMS_TO_TICKS(3000));
        SetDeviceState(kDeviceStateUpgrading);
        board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
        display->SetChatMessage("system", Lang::Strings::PLEASE_WAIT);

        bool success = assets.Download(download_url, [this, display](int progress, size_t speed) -> void {
            char buffer[32];
            snprintf(buffer, sizeof(buffer), "%d%% %uKB/s", progress, speed / 1024);
            Schedule([display, message = std::string(buffer)]() {
                display->SetChatMessage("system", message.c_str());
            });
        });

        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        vTaskDelay(pdMS_TO_TICKS(1000));

        if (!success) {
            Alert(Lang::Strings::ERROR, Lang::Strings::DOWNLOAD_ASSETS_FAILED, "circle_xmark", Lang::Sounds::OGG_EXCLAMATION);
            vTaskDelay(pdMS_TO_TICKS(2000));
            SetDeviceState(kDeviceStateActivating);
            return;
        }
    }

    // Apply assets
    assets.Apply();
    display->SetChatMessage("system", "");
    display->SetEmotion("microchip_ai");
}

void Application::CheckNewVersion() {
    // 查询 OTA 配置的同时也会获得协议地址、服务器时间和设备激活信息。
    int retry_count = 0;
    int retry_delay = 2; // Keep startup retries bounded.

    auto& board = Board::GetInstance();
    while (true) {
        auto display = board.GetDisplay();
        while (!network_connected_.load()) {
            display->SetStatus("等待网络连接");
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
        display->SetStatus(Lang::Strings::CHECKING_NEW_VERSION);

        esp_err_t err = ota_->CheckVersion();
        if (err != ESP_OK) {
            retry_count++;
            Settings saved("websocket", false);
            if (!saved.GetString("url").empty()) {
                ESP_LOGW(TAG, "Configuration service unavailable; use saved WebSocket settings and retry in background");
                return;
            }
            // A first boot has no saved WebSocket address. Keep checking the
            // configuration service with bounded delays so starting Python
            // later recovers without power-cycling the hardware.
            display->SetStatus("等待语音服务连接");
            display->SetChatMessage("system", "语音服务暂不可用，恢复后会自动连接");
            ESP_LOGW(TAG, "Configuration unavailable: code=%d; retry in %d seconds (attempt=%d)", err, retry_delay, retry_count);
            for (int i = 0; i < retry_delay; i++) {
                vTaskDelay(pdMS_TO_TICKS(1000));
                if (GetDeviceState() == kDeviceStateIdle) {
                    break;
                }
            }
            retry_delay = std::min(retry_delay * 2, 10);
            continue;
        }
        retry_count = 0;
        retry_delay = 10; // Reset retry delay

        if (ota_->HasNewVersion()) {
            if (UpgradeFirmware(ota_->GetFirmwareUrl(), ota_->GetFirmwareVersion())) {
                return; // This line will never be reached after reboot
            }
            // If upgrade failed, continue to normal operation
        }

        // No new version, mark the current version as valid
        ota_->MarkCurrentVersionValid();
        if (!ota_->HasActivationCode() && !ota_->HasActivationChallenge()) {
            // Exit the loop if done checking new version
            break;
        }

        display->SetStatus(Lang::Strings::ACTIVATION);
        // Activation code is shown to the user and waiting for the user to input
        if (ota_->HasActivationCode()) {
            ShowActivationCode(ota_->GetActivationCode(), ota_->GetActivationMessage());
        }

        // This will block the loop until the activation is done or timeout
        for (int i = 0; i < 10; ++i) {
            ESP_LOGI(TAG, "Activating... %d/%d", i + 1, 10);
            esp_err_t err = ota_->Activate();
            if (err == ESP_OK) {
                break;
            } else if (err == ESP_ERR_TIMEOUT) {
                vTaskDelay(pdMS_TO_TICKS(3000));
            } else {
                vTaskDelay(pdMS_TO_TICKS(10000));
            }
            if (GetDeviceState() == kDeviceStateIdle) {
                break;
            }
        }
    }
}

void Application::InitializeProtocol() {
    // 本项目只保留 WebSocket 通信链路，服务器下发的 MQTT 参数不再参与协议选择。
    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto codec = board.GetAudioCodec();

    display->SetStatus(Lang::Strings::LOADING_PROTOCOL);

    ++protocol_generation_;
    if (protocol_) protocol_->CloseAudioChannel();
    protocol_ = std::make_shared<WebsocketProtocol>();
    ESP_LOGI(TAG, "Service readiness revision=2026100502");
    ESP_LOGI(TAG, "Conversation lifecycle revision=2026100503");
    const auto active_protocol = protocol_.get();

    protocol_->OnConnected([this]() { Schedule([this]() { DismissAlert(); }); });

    protocol_->OnNetworkError([this](const std::string& message) {
        last_error_message_ = message;
        xEventGroupSetBits(event_group_, MAIN_EVENT_ERROR);
    });
    
    protocol_->OnIncomingAudio([this, active_protocol](std::unique_ptr<AudioStreamPacket> packet) {
        if (aborted_.load() || !active_protocol->IsAudioChannelOpened()) return;
        // 网络任务只把 Opus 包压入解码队列，实际解码和播放由 AudioService 完成。
        ESP_LOGD(TAG, "Audio packet received: size=%u, state=%d",
            static_cast<unsigned>(packet->payload.size()), static_cast<int>(GetDeviceState()));
        audio_service_.PushPacketToDecodeQueue(std::move(packet));
    });
    
    protocol_->OnAudioChannelOpened([this, codec, active_protocol]() {
        Schedule([this, codec, active_protocol]() {
            if (protocol_.get() != active_protocol || !protocol_->IsAudioChannelOpened()) return;
            Board::GetInstance().SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
            if (protocol_->server_sample_rate() != codec->output_sample_rate()) {
                ESP_LOGW(TAG, "Server/device sample rate differs; output will be resampled");
            }
        });
    });

    protocol_->OnAudioChannelClosed([this, active_protocol]() {
        aborted_ = true;
        ++playback_epoch_;
        audio_service_.ResetDecoder();
        const uint32_t closed_session = McpServer::GetInstance().Session();
        Schedule([this, active_protocol, closed_session]() {
            if (protocol_.get() != active_protocol || closed_session != McpServer::GetInstance().Session()) return;
            StopUnavailableConversation();
        });
    });

    protocol_->OnIncomingJson([this, display](const cJSON* root) {
        // 控制消息在协议回调任务到达；涉及状态/UI 的动作必须再次 Schedule。
        auto type = cJSON_GetObjectItem(root, "type");
        if (!cJSON_IsString(type)) return;
        if (strcmp(type->valuestring, "wake_word_config") == 0) {
            auto request = cJSON_GetObjectItem(root, "requestId");
            auto word = cJSON_GetObjectItem(root, "word");
            auto pinyin = cJSON_GetObjectItem(root, "pinyin");
            if (!cJSON_IsString(request) || !cJSON_IsString(word) || !cJSON_IsString(pinyin) ||
                strlen(request->valuestring) > 64 || strlen(word->valuestring) > 24 || strlen(pinyin->valuestring) > 63) return;
            Schedule([this, id = std::string(request->valuestring), word = std::string(word->valuestring),
                      pinyin = std::string(pinyin->valuestring)]() {
                std::string status = "unsupported";
#ifdef CONFIG_USE_DYNAMIC_WAKE_WORD
                status = DynamicWakeWord::Instance().Apply(word, pinyin);
#endif
                if (protocol_) protocol_->SendWakeWordConfigResult(id, word, status);
            });
        } else if (strcmp(type->valuestring, "standby") == 0) {
            auto stream = cJSON_GetObjectItem(root, "stream_id");
            std::string stream_id = cJSON_IsString(stream) ? stream->valuestring : "";
            auto request = cJSON_GetObjectItem(root, "requestId");
            std::string request_id = cJSON_IsString(request) ? request->valuestring : "";
            const bool force = cJSON_IsTrue(cJSON_GetObjectItem(root, "force"));
            const auto epoch = playback_epoch_.load();
            Schedule([this, stream_id, request_id, force, epoch]() {
                if (!request_id.empty() && request_id == last_standby_request_id_) {
                    if (protocol_) protocol_->SendStandbyAck(request_id);
                    return;
                }
                if (epoch != playback_epoch_.load()) return;
                if (!force && !stream_id.empty() && !audio_stream_id_.empty() && stream_id != audio_stream_id_) return;
                conversation_awake_ = false;
                speech_listen_allowed_ = false;
                aborted_ = true;
                ++playback_epoch_;
                audio_service_.ResetDecoder();
                SetDeviceState(kDeviceStateIdle);
                last_standby_request_id_ = request_id;
                if (protocol_ && !request_id.empty()) protocol_->SendStandbyAck(request_id);
            });
        } else if (strcmp(type->valuestring, "status") == 0) {
            auto text = cJSON_GetObjectItem(root, "text");
            if (cJSON_IsString(text)) {
                Schedule([this, display, message = std::string(text->valuestring)]() {
                    if (GetDeviceState() == kDeviceStateIdle) { UpdateAvailability(true); return; }
                    display->SetStatus(message.c_str());
                });
            }
        } else if (strcmp(type->valuestring, "pong") == 0) {
            // pong 只证明传输仍在线，不能据此进入监听或延长一轮对话。
        } else if (strcmp(type->valuestring, "tts") == 0) {
            auto state = cJSON_GetObjectItem(root, "state");
            if (!cJSON_IsString(state)) return;
            auto media = cJSON_GetObjectItem(root, "media");
            const bool music = cJSON_IsString(media) && strcmp(media->valuestring, "music") == 0;
            const bool reminder = cJSON_IsString(media) && strcmp(media->valuestring, "reminder") == 0;
            auto title = cJSON_GetObjectItem(root, "music_title");
            std::string music_title = music && cJSON_IsString(title) ? title->valuestring : "";
            auto stream = cJSON_GetObjectItem(root, "stream_id");
            std::string stream_id = cJSON_IsString(stream) ? stream->valuestring : "";
            if (strcmp(state->valuestring, "start") == 0) {
                // A new start follows the previous stop on the ordered WebSocket.
                aborted_ = false;
                tts_active_ = true;
                const auto epoch = ++playback_epoch_;
                // Reset before accepting this stream's binary packets; doing
                // it later in the state-change task discards its first packets.
                audio_service_.ResetDecoder();
                const bool listen_after = cJSON_IsTrue(cJSON_GetObjectItem(root, "listen_after"));
                Schedule([this, music, reminder, stream_id, epoch, music_title, listen_after]() {
                    if (epoch != playback_epoch_.load() || aborted_.load()) return;
                    const bool was_music = music_playing_;
                    speech_listen_allowed_ = listen_after && conversation_awake_.load();
                    music_playing_ = music;
                    reminder_playing_ = reminder;
                    reminder_tone_ = false;
                    audio_stream_id_ = stream_id;
                    tts_sentence_started_ = false;
                    if (music) {
                        Board::GetInstance().SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
                        listening_mode_ = kListeningModeAutoStop;
                    }
                    SetDeviceState(kDeviceStateSpeaking);
                    // State changes can be a no-op when replacing a speaking stream.
                    UpdateSpeakingAudio();
                    // A music start can arrive while already in the speaking state.
                    auto display = Board::GetInstance().GetDisplay();
                    display->SetMusicTitle(music_title.c_str());
                    display->BeginAssistantResponse();
                    display->SetStatus(reminder ? "提醒中" : music ? "播放音乐" : Lang::Strings::SPEAKING);
                    if (music) display->SetEmotion("singing");
                    else if (reminder) display->SetEmotion("reminder_call");
                    else if (was_music) display->SetEmotion("neutral");
                });
            } else if (strcmp(state->valuestring, "reminder_wait") == 0 && reminder) {
                const auto epoch = playback_epoch_.load();
                Schedule([this, display, stream_id, epoch]() {
                    if (epoch != playback_epoch_.load() || aborted_.load() || !tts_active_ ||
                        !reminder_playing_ || stream_id.empty() || stream_id != audio_stream_id_) return;
                    reminder_tone_ = true;
                    display->SetStatus("提醒中");
                    display->SetEmotion("singing");
                });
            } else if (strcmp(state->valuestring, "stop") == 0) {
                auto listen = cJSON_GetObjectItem(root, "listen_after");
                const bool listen_after = !cJSON_IsFalse(listen);
                const bool interrupted = cJSON_IsTrue(cJSON_GetObjectItem(root, "aborted"));
                if (interrupted) {
                    aborted_ = true;
                    ++playback_epoch_;
                    audio_service_.ResetDecoder();
                }
                const auto epoch = playback_epoch_.load();
                Schedule([this, stream_id, listen_after, epoch]() {
                    if (epoch != playback_epoch_.load()) return;
                    // A speech completion from the previous turn must not
                    // change the status of the current music stream.
                    if (!stream_id.empty() && stream_id != audio_stream_id_) return;
                    tts_active_ = false;
                    music_playing_ = false;
                    reminder_playing_ = false;
                    reminder_tone_ = false;
                    Board::GetInstance().GetDisplay()->SetMusicTitle("");
                    if (GetDeviceState() == kDeviceStateSpeaking) {
                        if (!listen_after || !conversation_awake_.load() || listening_mode_ == kListeningModeManualStop) {
                            SetDeviceState(kDeviceStateIdle);
                        } else {
                            listening_display_phase_ = ListeningDisplayPhase::Waiting;
                            listening_voice_active_ = false;
                            listening_voice_hold_until_us_ = 0;
                            SetDeviceState(kDeviceStateListening);
                        }
                    }
                });
            } else if (strcmp(state->valuestring, "sentence_start") == 0) {
                auto text = cJSON_GetObjectItem(root, "text");
                if (cJSON_IsString(text)) {
                    ESP_LOGI(TAG, "<< %s", text->valuestring);
                    const auto epoch = playback_epoch_.load();
                    Schedule([this, display, stream_id, epoch, message = std::string(text->valuestring)]() {
                        if (epoch != playback_epoch_.load() || aborted_.load()) return;
                        if (!stream_id.empty() && stream_id != audio_stream_id_) {
                            // Older servers announce start before assigning the reply ID.
                            // Bind only the first speech subtitle; later mismatches are stale.
                            if (music_playing_ || tts_sentence_started_) return;
                            audio_stream_id_ = stream_id;
                        }
                        tts_sentence_started_ = true;
                        display->SetChatMessage("assistant", message.c_str());
                    });
                }
            }
        } else if (strcmp(type->valuestring, "asr") == 0) {
            auto state = cJSON_GetObjectItem(root, "state");
            if (cJSON_IsString(state)) {
                const auto epoch = playback_epoch_.load();
                Schedule([this, state = std::string(state->valuestring), epoch]() {
                    if (epoch != playback_epoch_.load() || GetDeviceState() != kDeviceStateListening ||
                            !conversation_awake_.load()) return;
                    if (state == "start") listening_display_phase_ = ListeningDisplayPhase::Recognizing;
                    else if (state == "empty") listening_display_phase_ = ListeningDisplayPhase::Empty;
                    else if (state == "timeout") listening_display_phase_ = ListeningDisplayPhase::Timeout;
                    else if (state == "error") listening_display_phase_ = ListeningDisplayPhase::Error;
                    else return;
                    listening_voice_hold_until_us_ = 0;
                    UpdateListeningStatus();
                });
            }
        } else if (strcmp(type->valuestring, "stt") == 0) {
            auto text = cJSON_GetObjectItem(root, "text");
            if (cJSON_IsString(text)) {
                ESP_LOGI(TAG, ">> %s", text->valuestring);
                Schedule([this, display, message = std::string(text->valuestring)]() {
                    display->SetChatMessage("user", message.c_str());
                    if (GetDeviceState() == kDeviceStateListening) {
                        listening_display_phase_ = ListeningDisplayPhase::Thinking;
                        listening_voice_hold_until_us_ = 0;
                        UpdateListeningStatus();
                    } else {
                        display->SetStatus("正在思考");
                    }
                    display->SetEmotion("thinking");
                });
            }
        } else if (strcmp(type->valuestring, "llm") == 0) {
            auto emotion = cJSON_GetObjectItem(root, "emotion");
            if (cJSON_IsString(emotion)) {
                Schedule([this, display, emotion_str = std::string(emotion->valuestring)]() {
                    if (music_playing_) return;
                    display->SetEmotion(emotion_str.c_str());
                });
            }
        } else if (strcmp(type->valuestring, "mcp") == 0) {
            auto payload = cJSON_GetObjectItem(root, "payload");
            if (cJSON_IsObject(payload)) {
                McpServer::GetInstance().ParseMessage(payload);
            }
        } else if (strcmp(type->valuestring, "system") == 0) {
            auto command = cJSON_GetObjectItem(root, "command");
            if (cJSON_IsString(command)) {
                ESP_LOGI(TAG, "System command: %s", command->valuestring);
                if (strcmp(command->valuestring, "reboot") == 0) {
                    // OTA 完成后服务端可要求重启；仍投递到主任务执行。
                    Schedule([this]() {
                        Reboot();
                    });
                } else {
                    ESP_LOGW(TAG, "Unknown system command: %s", command->valuestring);
                }
            }
        } else if (strcmp(type->valuestring, "alert") == 0) {
            auto status = cJSON_GetObjectItem(root, "status");
            auto message = cJSON_GetObjectItem(root, "message");
            auto emotion = cJSON_GetObjectItem(root, "emotion");
            if (cJSON_IsString(status) && cJSON_IsString(message) && cJSON_IsString(emotion)) {
                Alert(status->valuestring, message->valuestring, emotion->valuestring, Lang::Sounds::OGG_VIBRATION);
            } else {
                ESP_LOGW(TAG, "Alert command requires status, message and emotion");
            }
#if CONFIG_RECEIVE_CUSTOM_MESSAGE
        } else if (strcmp(type->valuestring, "custom") == 0) {
            auto payload = cJSON_GetObjectItem(root, "payload");
            ESP_LOGI(TAG, "Received custom message: %s", cJSON_PrintUnformatted(root));
            if (cJSON_IsObject(payload)) {
                Schedule([this, display, payload_str = std::string(cJSON_PrintUnformatted(payload))]() {
                    display->SetChatMessage("system", payload_str.c_str());
                });
            } else {
                ESP_LOGW(TAG, "Invalid custom message format: missing payload");
            }
#endif
        } else {
            ESP_LOGW(TAG, "Unknown message type: %s", type->valuestring);
        }
    });
    
    protocol_->Start();
}

void Application::ShowActivationCode(const std::string& code, const std::string& message) {
    struct digit_sound {
        char digit;
        const std::string_view& sound;
    };
    static const std::array<digit_sound, 10> digit_sounds{{
        digit_sound{'0', Lang::Sounds::OGG_0},
        digit_sound{'1', Lang::Sounds::OGG_1}, 
        digit_sound{'2', Lang::Sounds::OGG_2},
        digit_sound{'3', Lang::Sounds::OGG_3},
        digit_sound{'4', Lang::Sounds::OGG_4},
        digit_sound{'5', Lang::Sounds::OGG_5},
        digit_sound{'6', Lang::Sounds::OGG_6},
        digit_sound{'7', Lang::Sounds::OGG_7},
        digit_sound{'8', Lang::Sounds::OGG_8},
        digit_sound{'9', Lang::Sounds::OGG_9}
    }};

    // This sentence uses 9KB of SRAM, so we need to wait for it to finish
    Alert(Lang::Strings::ACTIVATION, message.c_str(), "link", Lang::Sounds::OGG_ACTIVATION);

    for (const auto& digit : code) {
        auto it = std::find_if(digit_sounds.begin(), digit_sounds.end(),
            [digit](const digit_sound& ds) { return ds.digit == digit; });
        if (it != digit_sounds.end()) {
            audio_service_.PlaySound(it->sound);
        }
    }
}

void Application::Alert(const char* status, const char* message, const char* emotion, const std::string_view& sound) {
    ESP_LOGW(TAG, "Alert [%s] %s: %s", emotion, status, message);
    auto display = Board::GetInstance().GetDisplay();
    display->SetStatus(status);
    display->SetEmotion(emotion);
    display->SetChatMessage("system", message);
    if (!sound.empty()) {
        audio_service_.PlaySound(sound);
    }
}

DeviceReadiness Application::GetReadiness() const {
    DeviceReadiness readiness;
    readiness.network = network_connected_;
    readiness.voice_service = protocol_ && protocol_->IsAudioChannelOpened();
    readiness.connecting = connection_task_running_;
    readiness.management = protocol_ ? protocol_->GetManagementServiceState() : ManagementServiceState::Unknown;
    readiness.chassis = Board::GetInstance().IsChassisConnected();
    readiness.chassis_seen = chassis_ever_connected_;
    return readiness;
}

bool Application::IsSystemReady() const { return GetReadiness().Ready(); }

void Application::UpdateAvailability(bool force) {
    auto& board = Board::GetInstance();
    const auto readiness = GetReadiness();
    if (readiness.chassis) chassis_ever_connected_ = true;
    const bool ready = readiness.Ready();
    const std::string status = ready ? Lang::Strings::STANDBY : readiness.UnavailableStatus();
    const auto state = GetDeviceState();
    const bool changed = status != availability_status_;
    availability_status_ = status;
    if (changed) ESP_LOGI(TAG, "Availability: %s", status.c_str());
    if (state == kDeviceStateIdle && (force || idle_status_ != status)) {
        idle_status_ = status;
        board.GetDisplay()->SetStatus(status.c_str());
    } else if (changed && !ready && (state == kDeviceStateListening || state == kDeviceStateSpeaking)) {
        board.GetDisplay()->ShowNotification(status.c_str());
    }
    if (ready && !ready_announced_ && state == kDeviceStateIdle) {
        ready_announced_ = true;
        audio_service_.PlaySound(Lang::Sounds::OGG_SUCCESS);
    }
}

void Application::StopUnavailableConversation() {
    conversation_awake_ = false;
    speech_listen_allowed_ = false;
    aborted_ = true;
    ++playback_epoch_;
    tts_active_ = false;
    music_playing_ = false;
    reminder_playing_ = false;
    reminder_tone_ = false;
    play_popup_on_listening_ = false;
    pending_wake_ = false;
    pending_wake_word_.clear();
    audio_stream_id_.clear();
    audio_service_.ResetDecoder();
    while (audio_service_.PopPacketFromSendQueue());
    const auto state = GetDeviceState();
    if (state == kDeviceStateConnecting || state == kDeviceStateListening || state == kDeviceStateSpeaking || state == kDeviceStateIdle) {
        audio_service_.EnableVoiceProcessing(false);
        audio_service_.EnableWakeWordDetection(true);
        Board::GetInstance().SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        SetDeviceState(kDeviceStateIdle);
        UpdateAvailability(true);
    }
}

void Application::StartConnectionTask() {
    if (!protocol_ || !network_connected_ || connection_task_running_) return;
    struct ConnectionTask {
        Application* app;
        std::shared_ptr<Protocol> protocol;
        uint32_t generation;
    };
    auto context = new ConnectionTask{this, protocol_, protocol_generation_};
    connection_task_running_ = true;
    const auto created = xTaskCreate([](void* arg) {
        std::unique_ptr<ConnectionTask> context(static_cast<ConnectionTask*>(arg));
        auto app = context->app;
        auto protocol = context->protocol;
        const auto generation = context->generation;
        const bool connected = protocol->OpenAudioChannel();
        app->Schedule([app, protocol, generation, connected]() {
            // ResetProtocol can cancel this worker, but must not start another
            // socket with the same network id until this worker has finished.
            app->connection_task_running_ = false;
            if (app->protocol_generation_ != generation || app->protocol_ != protocol) return;
            if (!connected || !app->network_connected_ || !protocol->IsAudioChannelOpened()) {
                app->StopUnavailableConversation();
                return;
            }
            if (app->GetDeviceState() == kDeviceStateConnecting) {
                if (app->pending_wake_) app->ContinueWakeWordInvoke(app->pending_wake_word_);
                else app->ContinueOpenAudioChannel(app->pending_listening_mode_);
                app->pending_wake_ = false;
                app->pending_wake_word_.clear();
            } else if (app->GetDeviceState() == kDeviceStateIdle) {
                protocol->EnterStandby();
                Board::GetInstance().SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
            } else if (app->GetDeviceState() != kDeviceStateListening && app->GetDeviceState() != kDeviceStateSpeaking) {
                protocol->CloseAudioChannel();
            }
            app->UpdateAvailability(true);
        });
        protocol.reset();
        context.reset();
        vTaskDelete(nullptr);
    }, "service_connect", 6144, context, 2, nullptr);
    if (created != pdPASS) {
        delete context;
        connection_task_running_ = false;
        StopUnavailableConversation();
    }
}

void Application::DismissAlert() {
    if (GetDeviceState() == kDeviceStateIdle) {
        auto display = Board::GetInstance().GetDisplay();
        UpdateAvailability(true);
        display->SetEmotion("neutral");
        display->SetChatMessage("system", "");
    }
}

void Application::ToggleChatState() {
    xEventGroupSetBits(event_group_, MAIN_EVENT_TOGGLE_CHAT);
}

void Application::StartListening() {
    xEventGroupSetBits(event_group_, MAIN_EVENT_START_LISTENING);
}

void Application::StopListening() {
    xEventGroupSetBits(event_group_, MAIN_EVENT_STOP_LISTENING);
}

void Application::HandleToggleChatEvent() {
    // 按钮只控制已唤醒的对话；待命时仍需语音唤醒词。
    auto state = GetDeviceState();
    
    if (state == kDeviceStateActivating) {
        Board::GetInstance().GetDisplay()->ShowNotification("正在连接服务，请稍候");
        return;
    } else if (state == kDeviceStateWifiConfiguring) {
        audio_service_.EnableAudioTesting(true);
        SetDeviceState(kDeviceStateAudioTesting);
        return;
    } else if (state == kDeviceStateAudioTesting) {
        audio_service_.EnableAudioTesting(false);
        SetDeviceState(kDeviceStateWifiConfiguring);
        return;
    }

    if (!protocol_) {
        ESP_LOGE(TAG, "Protocol not initialized");
        return;
    }

    if (state == kDeviceStateIdle || !conversation_awake_.load()) {
        Board::GetInstance().GetDisplay()->ShowNotification("请先说唤醒词");
        return;
    } else if (state == kDeviceStateSpeaking) {
        AbortSpeaking(kAbortReasonNone);
    } else if (state == kDeviceStateListening) {
        protocol_->EnterStandby();
    }
}

void Application::ContinueOpenAudioChannel(ListeningMode mode) {
    // 回调排队期间状态可能已改变，因此联网前必须二次确认。
    if (GetDeviceState() != kDeviceStateConnecting) {
        return;
    }

    if (!protocol_ || !network_connected_) {
        StopUnavailableConversation();
        return;
    }
    if (!protocol_->IsAudioChannelOpened()) {
        pending_wake_ = false;
        pending_listening_mode_ = mode;
        StartConnectionTask();
        return;
    }
    SetListeningMode(mode);
}

void Application::HandleStartListeningEvent() {
    // 外部显式开始监听使用手动停止模式，与唤醒词触发的自动 VAD 模式区分。
    auto state = GetDeviceState();
    
    if (state == kDeviceStateActivating) {
        Board::GetInstance().GetDisplay()->ShowNotification("正在连接服务，请稍候");
        return;
    } else if (state == kDeviceStateWifiConfiguring) {
        audio_service_.EnableAudioTesting(true);
        SetDeviceState(kDeviceStateAudioTesting);
        return;
    }

    if (!protocol_) {
        ESP_LOGE(TAG, "Protocol not initialized");
        return;
    }
    
    if (!conversation_awake_.load()) {
        Board::GetInstance().GetDisplay()->ShowNotification("请先说唤醒词");
        return;
    } else if (state == kDeviceStateSpeaking) {
        AbortSpeaking(kAbortReasonNone);
        SetListeningMode(kListeningModeManualStop);
    }
}

void Application::HandleStopListeningEvent() {
    auto state = GetDeviceState();
    
    if (state == kDeviceStateAudioTesting) {
        audio_service_.EnableAudioTesting(false);
        SetDeviceState(kDeviceStateWifiConfiguring);
        return;
    } else if (state == kDeviceStateListening) {   
        if (protocol_) {
            protocol_->SendStopListening();
        }
        SetDeviceState(kDeviceStateIdle);
    }
}

void Application::HandleWakeWordDetectedEvent() {
    // 唤醒词在空闲态开启新会话，在说话/监听态则表示打断并开始新一轮。
    if (!protocol_) {
        return;
    }

    auto state = GetDeviceState();
    auto wake_word = audio_service_.GetLastWakeWord();
    ESP_LOGI(TAG, "Wake word detected: %s (state: %d)", wake_word.c_str(), (int)state);

    if (state == kDeviceStateIdle) {
        audio_service_.EncodeWakeWord();
        auto wake_word = audio_service_.GetLastWakeWord();

        if (!protocol_->IsAudioChannelOpened()) {
            SetDeviceState(kDeviceStateConnecting);
            // 先显示 connecting，再执行可能阻塞约 1 秒的 WebSocket 握手。
            Schedule([this, wake_word]() {
                ContinueWakeWordInvoke(wake_word);
            });
            return;
        }
        // 待命常连接已经存在时无需重连，可直接发送唤醒控制消息。
        ContinueWakeWordInvoke(wake_word);
    } else if (state == kDeviceStateSpeaking || state == kDeviceStateListening) {
        conversation_awake_ = true;
        speech_listen_allowed_ = true;
        AbortSpeaking(kAbortReasonWakeWordDetected);
        // 清空上一轮尚未上传的 Opus 包，避免新会话收到残留语音。
        while (audio_service_.PopPacketFromSendQueue());

        if (state == kDeviceStateListening) {
            // Send the wake word to the server to start a new conversation
            protocol_->SendWakeWordDetected(wake_word);
            play_popup_on_listening_ = true;
            SetListeningMode(GetDefaultListeningMode());
        } else {
            // Play popup sound and start listening again
            play_popup_on_listening_ = true;
            SetListeningMode(GetDefaultListeningMode());
        }
    } else if (state == kDeviceStateActivating) {
        Board::GetInstance().GetDisplay()->ShowNotification("正在连接服务，请稍候");
    }
}

void Application::ContinueWakeWordInvoke(const std::string& wake_word) {
    // 与按钮联网路径一样，执行排队回调前先确认状态仍允许开启会话。
    if (GetDeviceState() != kDeviceStateConnecting &&
        GetDeviceState() != kDeviceStateIdle) {
        return;
    }

    if (!protocol_ || !network_connected_) {
        StopUnavailableConversation();
        return;
    }
    if (!protocol_->IsAudioChannelOpened()) {
        pending_wake_ = true;
        pending_wake_word_ = wake_word;
        StartConnectionTask();
        return;
    }

    conversation_awake_ = true;
    speech_listen_allowed_ = true;
    // Send the actual wake event even when optional wake audio is disabled.
    protocol_->SendWakeWordDetected(wake_word);
    ESP_LOGI(TAG, "Wake word detected: %s", wake_word.c_str());
#if CONFIG_SEND_WAKE_WORD_DATA
    // 可选上传唤醒词前后的音频，让服务端获得更完整的首句上下文。
    while (auto packet = audio_service_.PopWakeWordPacket()) {
        protocol_->SendAudio(std::move(packet));
    }
#else
    // 提示音延后到 Listening 状态播放，否则 EnableVoiceProcessing 重置解码器时会清掉它。
    play_popup_on_listening_ = true;
#endif
    SetListeningMode(GetDefaultListeningMode());
}

void Application::HandleStateChangedEvent() {
    // 这里是“状态 -> UI、麦克风、唤醒词检测、电源模式”的唯一集中映射。
    DeviceState new_state = state_machine_.GetState();
    clock_ticks_ = 0;

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto led = board.GetLed();
    led->OnStateChanged();
    
    switch (new_state) {
        case kDeviceStateUnknown:
        case kDeviceStateIdle:
            listening_display_phase_ = ListeningDisplayPhase::Waiting;
            listening_voice_active_ = false;
            listening_voice_hold_until_us_ = 0;
            conversation_awake_ = false;
            speech_listen_allowed_ = false;
            display->SetMusicTitle("");
            board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
            tts_active_ = false;
            music_playing_ = false;
            reminder_playing_ = false;
            reminder_tone_ = false;
            UpdateAvailability(true);
            display->ClearChatMessages();
            display->SetEmotion("neutral");
            audio_service_.EnableVoiceProcessing(false);
            audio_service_.EnableWakeWordDetection(true);
            if (protocol_ && protocol_->IsAudioChannelOpened()) {
                protocol_->EnterStandby();
            }
            break;
        case kDeviceStateConnecting:
            display->SetStatus(Lang::Strings::CONNECTING);
            display->SetEmotion("neutral");
            display->SetChatMessage("system", "");
            break;
        case kDeviceStateListening:
            if (!conversation_awake_.load()) {
                SetDeviceState(kDeviceStateIdle);
                break;
            }
            display->SetMusicTitle("");
            music_playing_ = false;
            reminder_playing_ = false;
            reminder_tone_ = false;
#ifndef CONFIG_USE_ROBO_EYES
            display->SetEmotion("neutral");
#endif
            UpdateListeningStatus();

            // 【修复】每次进入 Listening 都无条件启用麦克风并通知服务器。
            // 旧代码用 play_popup_on_listening_ || !IsAudioProcessorRunning() 守卫，
            // 但 TTS stop → Listening 时音频处理器可能因异步延迟尚在运行，
            // 导致 SendStartListening / EnableVoiceProcessing(true) 被跳过，
            // 用户第二轮说话麦克风未启动，Python 侧收不到音频 → 无回复。
            if (listening_mode_ == kListeningModeAutoStop) {
                audio_service_.WaitForPlaybackQueueEmpty();
                // Ensure audio processor is fully stopped before enabling
                audio_service_.EnableVoiceProcessing(false);
            }

            // Full duplex already has an active listen session. Restarting it
            // here loses the first syllables of the utterance that interrupted TTS.
            if (listening_mode_ != kListeningModeRealtime || !audio_service_.IsAudioProcessorRunning()) {
                protocol_->SendStartListening(listening_mode_);
                audio_service_.EnableVoiceProcessing(true);
            }

#ifdef CONFIG_WAKE_WORD_DETECTION_IN_LISTENING
            // Enable wake word detection in listening mode (configured via Kconfig)
            audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
#else
            // Disable wake word detection in listening mode
            audio_service_.EnableWakeWordDetection(false);
#endif
            
            // Play popup sound after ResetDecoder (in EnableVoiceProcessing) has been called
            if (play_popup_on_listening_) {
                play_popup_on_listening_ = false;
                audio_service_.PlaySound(Lang::Sounds::OGG_POPUP);
            }
            break;
        case kDeviceStateSpeaking:
            listening_display_phase_ = ListeningDisplayPhase::Waiting;
            listening_voice_active_ = false;
            listening_voice_hold_until_us_ = 0;
            display->SetStatus(reminder_playing_ ? "提醒中" : music_playing_ ? "播放音乐" : Lang::Strings::SPEAKING);
            if (music_playing_) display->SetEmotion("singing");
            else if (reminder_playing_) display->SetEmotion(reminder_tone_ ? "singing" : "reminder_call");

            UpdateSpeakingAudio();
            break;
        case kDeviceStateWifiConfiguring:
            audio_service_.EnableVoiceProcessing(false);
            audio_service_.EnableWakeWordDetection(false);
            break;
        default:
            // Do nothing
            break;
    }
}

void Application::UpdateListeningStatus() {
    if (GetDeviceState() != kDeviceStateListening || !conversation_awake_.load()) return;
    const char* status = Lang::Strings::LISTENING;
    if (audio_service_.IsVoiceDetected()) {
        listening_voice_active_ = true;
        status = "正在听你说";
    } else {
        switch (listening_display_phase_) {
            case ListeningDisplayPhase::Recognizing: status = "正在识别"; break;
            case ListeningDisplayPhase::Thinking: status = "正在思考"; break;
            case ListeningDisplayPhase::Empty: status = "没听清，请再说一次"; break;
            case ListeningDisplayPhase::Timeout: status = "识别超时，请再说一次"; break;
            case ListeningDisplayPhase::Error: status = "识别失败，请再说一次"; break;
            case ListeningDisplayPhase::Waiting:
                if (esp_timer_get_time() < listening_voice_hold_until_us_) status = "正在听你说";
                break;
        }
    }
    Board::GetInstance().GetDisplay()->SetStatus(status);
}

void Application::UpdateSpeakingAudio() {
    if (conversation_awake_.load() && speech_listen_allowed_ &&
            !music_playing_ && aec_mode_ != kAecOff) {
        listening_mode_ = kListeningModeRealtime;
        if (!audio_service_.IsAudioProcessorRunning()) {
            protocol_->SendStartListening(listening_mode_);
            audio_service_.EnableVoiceProcessing(true, false);
        }
        audio_service_.EnableWakeWordDetection(false);
    } else {
        audio_service_.EnableVoiceProcessing(false);
        audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
    }
}

void Application::Schedule(std::function<void()>&& callback) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        main_tasks_.push_back(std::move(callback));
    }
    xEventGroupSetBits(event_group_, MAIN_EVENT_SCHEDULE);
}

void Application::AbortSpeaking(AbortReason reason) {
    ESP_LOGI(TAG, "Abort speaking");
    aborted_ = true;
    ++playback_epoch_;
    tts_active_ = false;
    reminder_playing_ = false;
    reminder_tone_ = false;
    audio_service_.ResetDecoder();
    if (protocol_) {
        protocol_->SendAbortSpeaking(reason);
    }
}

void Application::SetListeningMode(ListeningMode mode) {
    if (!conversation_awake_.load()) {
        SetDeviceState(kDeviceStateIdle);
        return;
    }
    if (!protocol_ || !network_connected_ || !protocol_->IsAudioChannelOpened()) {
        StopUnavailableConversation();
        return;
    }
    if (GetDeviceState() != kDeviceStateListening) {
        listening_display_phase_ = ListeningDisplayPhase::Waiting;
        listening_voice_active_ = false;
        listening_voice_hold_until_us_ = 0;
    }
    listening_mode_ = mode;
    SetDeviceState(kDeviceStateListening);
}

ListeningMode Application::GetDefaultListeningMode() const {
    return aec_mode_ == kAecOff ? kListeningModeAutoStop : kListeningModeRealtime;
}

void Application::Reboot() {
    ESP_LOGI(TAG, "Rebooting...");
    ++protocol_generation_;
    // Cancel a handshake as well as an established channel.
    if (protocol_) {
        protocol_->CloseAudioChannel();
    }
    protocol_.reset();
    audio_service_.Stop();

    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
}

bool Application::UpgradeFirmware(const std::string& url, const std::string& version) {
    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();

    std::string upgrade_url = url;
    std::string version_info = version.empty() ? "(Manual upgrade)" : version;

    // Close audio channel if it's open
    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        ESP_LOGI(TAG, "Closing audio channel before firmware upgrade");
        protocol_->CloseAudioChannel();
    }
    ESP_LOGI(TAG, "Starting firmware upgrade from URL: %s", upgrade_url.c_str());

    Alert(Lang::Strings::OTA_UPGRADE, Lang::Strings::UPGRADING, "download", Lang::Sounds::OGG_UPGRADE);
    vTaskDelay(pdMS_TO_TICKS(3000));

    SetDeviceState(kDeviceStateUpgrading);

    std::string message = std::string(Lang::Strings::NEW_VERSION) + version_info;
    display->SetChatMessage("system", message.c_str());

    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
    audio_service_.Stop();
    vTaskDelay(pdMS_TO_TICKS(1000));

    bool upgrade_success = Ota::Upgrade(upgrade_url, [this, display](int progress, size_t speed) {
        char buffer[32];
        snprintf(buffer, sizeof(buffer), "%d%% %uKB/s", progress, speed / 1024);
        Schedule([display, message = std::string(buffer)]() {
            display->SetChatMessage("system", message.c_str());
        });
    });

    if (!upgrade_success) {
        // Upgrade failed, restart audio service and continue running
        ESP_LOGE(TAG, "Firmware upgrade failed, restarting audio service and continuing operation...");
        audio_service_.Start(); // Restart audio service
        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER); // Restore power save level
        Alert(Lang::Strings::ERROR, Lang::Strings::UPGRADE_FAILED, "circle_xmark", Lang::Sounds::OGG_EXCLAMATION);
        vTaskDelay(pdMS_TO_TICKS(3000));
        return false;
    } else {
        // Upgrade success, reboot immediately
        ESP_LOGI(TAG, "Firmware upgrade successful, rebooting...");
        display->SetChatMessage("system", "Upgrade successful, rebooting...");
        vTaskDelay(pdMS_TO_TICKS(1000)); // Brief pause to show message
        Reboot();
        return true;
    }
}

void Application::WakeWordInvoke(const std::string& wake_word) {
    if (!protocol_) {
        return;
    }

    auto state = GetDeviceState();
    
    if (state == kDeviceStateIdle) {
        audio_service_.EncodeWakeWord();

        if (!protocol_->IsAudioChannelOpened()) {
            SetDeviceState(kDeviceStateConnecting);
            // Schedule to let the state change be processed first (UI update)
            Schedule([this, wake_word]() {
                ContinueWakeWordInvoke(wake_word);
            });
            return;
        }
        // Channel already opened, continue directly
        ContinueWakeWordInvoke(wake_word);
    } else if (state == kDeviceStateSpeaking || state == kDeviceStateListening) {
        Schedule([this, wake_word]() {
            if (GetDeviceState() != kDeviceStateSpeaking && GetDeviceState() != kDeviceStateListening) return;
            conversation_awake_ = true;
            speech_listen_allowed_ = true;
            AbortSpeaking(kAbortReasonWakeWordDetected);
            while (audio_service_.PopPacketFromSendQueue());
            protocol_->SendWakeWordDetected(wake_word);
            play_popup_on_listening_ = true;
            SetListeningMode(GetDefaultListeningMode());
        });
    }
}

bool Application::CanEnterSleepMode() {
    if (!IsSystemReady() || connection_task_running_ || McpServer::GetInstance().HasAsyncCall()) return false;
    if (GetDeviceState() != kDeviceStateIdle) {
        return false;
    }

    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        return false;
    }

    if (!audio_service_.IsIdle()) {
        return false;
    }

    // Now it is safe to enter sleep mode
    return true;
}

void Application::SendMcpMessage(const std::string& payload) {
    SendMcpMessage(payload, McpServer::GetInstance().CallSession());
}

void Application::SendMcpMessage(const std::string& payload, uint32_t session) {
    // Always schedule to run in main task for thread safety
    Schedule([this, payload, session]() {
        if (protocol_ && session == McpServer::GetInstance().Session() && protocol_->IsAudioChannelOpened()) {
            protocol_->SendMcpMessage(payload);
        }
    });
}

void Application::SetAecMode(AecMode mode) {
    aec_mode_ = mode;
    Schedule([this]() {
        auto& board = Board::GetInstance();
        auto display = board.GetDisplay();
        switch (aec_mode_) {
        case kAecOff:
            audio_service_.EnableDeviceAec(false);
            display->ShowNotification(Lang::Strings::RTC_MODE_OFF);
            break;
        case kAecOnServerSide:
            audio_service_.EnableDeviceAec(false);
            display->ShowNotification(Lang::Strings::RTC_MODE_ON);
            break;
        case kAecOnDeviceSide:
            audio_service_.EnableDeviceAec(true);
            display->ShowNotification(Lang::Strings::RTC_MODE_ON);
            break;
        }

        // If the AEC mode is changed, close the audio channel
        if (protocol_ && protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
    });
}

void Application::PlaySound(const std::string_view& sound) {
    audio_service_.PlaySound(sound);
}

void Application::ResetProtocol() {
    Schedule([this]() {
        ++protocol_generation_;
        // Cancel an in-progress handshake as well as an established channel.
        if (protocol_) {
            protocol_->CloseAudioChannel();
        }
        // Reset protocol
        protocol_.reset();
        StopUnavailableConversation();
    });
}

