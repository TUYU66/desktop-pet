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
                        Reboot("server_command");
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
