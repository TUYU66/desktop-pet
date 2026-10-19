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

void Application::Reboot(const char* reason) {
    ESP_LOGW(TAG, "Rebooting: reason=%s", reason);
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
        Reboot("firmware_upgrade");
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

