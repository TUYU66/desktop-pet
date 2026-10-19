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
