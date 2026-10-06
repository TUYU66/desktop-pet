#include "afe_audio_processor.h"
#include <esp_log.h>
#include <esp_timer.h>
#include <cstdint>

#define PROCESSOR_RUNNING 0x01

#define TAG "AfeAudioProcessor"

AfeAudioProcessor::AfeAudioProcessor()
    : afe_data_(nullptr) {
    event_group_ = xEventGroupCreate();
}

void AfeAudioProcessor::Initialize(AudioCodec* codec, int frame_duration_ms, srmodel_list_t* models_list) {
    codec_ = codec;
    frame_samples_ = frame_duration_ms * 16000 / 1000;

    // Pre-allocate output buffer capacity
    output_buffer_.reserve(frame_samples_);

    int ref_num = codec_->input_reference() ? 1 : 0;

    std::string input_format;
    for (int i = 0; i < codec_->input_channels() - ref_num; i++) {
        input_format.push_back('M');
    }
    for (int i = 0; i < ref_num; i++) {
        input_format.push_back('R');
    }
#ifdef CONFIG_USE_DEVICE_AEC
    // The compact board has separate microphone/speaker I2S and no hardware R.
    // Feed the PCM actually sent to the speaker as a second AFE channel.
    software_reference_ = !codec_->input_reference() && codec_->input_channels() == 1;
    if (software_reference_) input_format.push_back('R');
#endif
    feed_channels_ = input_format.size();
    ESP_LOGI(TAG, "Voice pipeline 2026100402: VAD=1 speech_min=64ms reference=%s",
        software_reference_ ? "software" : (codec_->input_reference() ? "hardware" : "none"));

    srmodel_list_t *models;
    if (models_list == nullptr) {
        models = esp_srmodel_init("model");
    } else {
        models = models_list;
    }

    char* ns_model_name = esp_srmodel_filter(models, ESP_NSNET_PREFIX, NULL);
    char* vad_model_name = esp_srmodel_filter(models, ESP_VADN_PREFIX, NULL);
    
    afe_config_t* afe_config = afe_config_init(input_format.c_str(), NULL, AFE_TYPE_VC, AFE_MODE_HIGH_PERF);
    afe_config->aec_mode = AEC_MODE_VOIP_HIGH_PERF;
    // Quiet/short Chinese commands should reach VAD; playback echo is rejected
    // separately by the reference-based gate below.
    afe_config->vad_mode = VAD_MODE_1;
    afe_config->vad_min_noise_ms = 160;
    afe_config->vad_min_speech_ms = 64;
    afe_config->vad_mute_playback = false;
    if (vad_model_name != nullptr) {
        afe_config->vad_model_name = vad_model_name;
    }

    if (ns_model_name != nullptr) {
        afe_config->ns_init = true;
        afe_config->ns_model_name = ns_model_name;
        afe_config->afe_ns_mode = AFE_NS_MODE_NET;
    } else {
        afe_config->ns_init = false;
    }

    afe_config->agc_init = false;
    afe_config->memory_alloc_mode = AFE_MEMORY_ALLOC_MORE_PSRAM;

#ifdef CONFIG_USE_DEVICE_AEC
    afe_config->aec_init = true;
    afe_config->vad_init = true;
#else
    afe_config->aec_init = false;
    afe_config->vad_init = true;
#endif

    afe_iface_ = esp_afe_handle_from_config(afe_config);
    afe_data_ = afe_iface_->create_from_config(afe_config);
    
    xTaskCreate([](void* arg) {
        auto this_ = (AfeAudioProcessor*)arg;
        this_->AudioProcessorTask();
        vTaskDelete(NULL);
    }, "audio_communication", 4096, this, 3, NULL);
}

AfeAudioProcessor::~AfeAudioProcessor() {
    if (afe_data_ != nullptr) {
        afe_iface_->destroy(afe_data_);
    }
    vEventGroupDelete(event_group_);
}

size_t AfeAudioProcessor::GetFeedSize() {
    if (afe_data_ == nullptr) {
        return 0;
    }
    return afe_iface_->get_feed_chunksize(afe_data_);
}

void AfeAudioProcessor::Feed(std::vector<int16_t>&& data) {
    if (afe_data_ == nullptr) {
        return;
    }

    std::lock_guard<std::mutex> lock(input_buffer_mutex_);
    // Check running state inside lock to avoid TOCTOU race with Stop()
    if (!IsRunning()) {
        return;
    }
    if (software_reference_) {
        for (auto sample : data) {
            input_buffer_.push_back(sample);
            input_buffer_.push_back(playback_reference_.empty() ? 0 : playback_reference_.front());
            if (!playback_reference_.empty()) playback_reference_.pop_front();
        }
    } else {
        if (codec_->input_reference() && codec_->input_channels() == 2) {
            barge_in_gate_.AddReference(data.data() + 1, data.size() / 2, esp_timer_get_time(), 2);
        }
        input_buffer_.insert(input_buffer_.end(), data.begin(), data.end());
    }
    size_t chunk_size = afe_iface_->get_feed_chunksize(afe_data_) * feed_channels_;
    while (input_buffer_.size() >= chunk_size) {
        afe_iface_->feed(afe_data_, input_buffer_.data());
        input_buffer_.erase(input_buffer_.begin(), input_buffer_.begin() + chunk_size);
    }
}

void AfeAudioProcessor::FeedPlaybackReference(const std::vector<int16_t>& data) {
    std::lock_guard<std::mutex> lock(input_buffer_mutex_);
    if (!software_reference_ || !IsRunning()) return;
    const auto now = esp_timer_get_time();
    barge_in_gate_.AddReference(data.data(), data.size(), now);
    if (now - last_reference_us_ > 160000) playback_reference_.clear();
    last_reference_us_ = now;
    // At most 200 ms; an input stall must not retain seconds of stale echo.
    constexpr size_t max_samples = 16000 / 5;
    for (auto sample : data) {
        if (playback_reference_.size() == max_samples) playback_reference_.pop_front();
        playback_reference_.push_back(sample);
    }
}

void AfeAudioProcessor::Start() {
    xEventGroupSetBits(event_group_, PROCESSOR_RUNNING);
}

void AfeAudioProcessor::Stop() {
    xEventGroupClearBits(event_group_, PROCESSOR_RUNNING);
    ++processing_generation_;

    std::lock_guard<std::mutex> lock(input_buffer_mutex_);
    if (afe_data_ != nullptr) {
        afe_iface_->reset_buffer(afe_data_);
    }
    input_buffer_.clear();
    playback_reference_.clear();
    last_reference_us_ = 0;
    is_speaking_ = false;
    barge_in_gate_.Reset();
}

bool AfeAudioProcessor::IsRunning() {
    return xEventGroupGetBits(event_group_) & PROCESSOR_RUNNING;
}

void AfeAudioProcessor::OnOutput(std::function<void(std::vector<int16_t>&& data)> callback) {
    output_callback_ = callback;
}

void AfeAudioProcessor::OnVadStateChange(std::function<void(bool speaking)> callback) {
    vad_state_change_callback_ = callback;
}

void AfeAudioProcessor::AudioProcessorTask() {
    auto fetch_size = afe_iface_->get_fetch_chunksize(afe_data_);
    auto feed_size = afe_iface_->get_feed_chunksize(afe_data_);
    ESP_LOGI(TAG, "Audio communication task started, feed size: %d fetch size: %d",
        feed_size, fetch_size);
    uint32_t observed_generation = processing_generation_.load();
    int64_t last_guard_log_us = 0;
    int64_t last_input_log_us = 0;

    while (true) {
        xEventGroupWaitBits(event_group_, PROCESSOR_RUNNING, pdFALSE, pdTRUE, portMAX_DELAY);

        const auto generation = processing_generation_.load();
        auto res = afe_iface_->fetch_with_delay(afe_data_, portMAX_DELAY);
        if ((xEventGroupGetBits(event_group_) & PROCESSOR_RUNNING) == 0 || generation != processing_generation_.load()) {
            continue;
        }
        if (res == nullptr || res->ret_value == ESP_FAIL || res->data == nullptr || res->data_size <= 0) {
            if (res != nullptr) ESP_LOGI(TAG, "Error code: %d", res->ret_value);
            continue;
        }
        if (generation != observed_generation) {
            output_buffer_.clear();
            speech_preroll_.clear();
            observed_generation = generation;
        }
        const size_t samples = res->data_size / sizeof(int16_t);
        const auto now = esp_timer_get_time();
        const auto decision = barge_in_gate_.Evaluate(res->data, samples, res->vad_state == VAD_SPEECH, now);
        const auto gate_us = esp_timer_get_time() - now;
        if (gate_us > 10000 && now - last_guard_log_us > 1000000) {
            const auto logged_us = gate_us > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(gate_us);
            ESP_LOGW(TAG, "Voice timing: barge_gate_us=%lu", static_cast<unsigned long>(logged_us));
            last_guard_log_us = now;
        }
        if (generation != processing_generation_.load()) continue;
        if (now - last_input_log_us > 5000000) {
            ESP_LOGI(TAG, "Voice input: vad=%d near_end=%d guarded=%d rms=%.0f correlation=%.2f",
                res->vad_state == VAD_SPEECH, decision.speech, decision.guarding,
                decision.rms, decision.correlation);
            last_input_log_us = now;
        }
        if (decision.guarding && res->vad_state == VAD_SPEECH && !decision.candidate && now - last_guard_log_us > 1000000) {
            ESP_LOGI(TAG, "Echo guard suppressed VAD: rms=%.0f correlation=%.2f", decision.rms, decision.correlation);
            last_guard_log_us = now;
        }
        // VAD state change
        if (vad_state_change_callback_) {
            if (decision.speech && !is_speaking_) {
                is_speaking_ = true;
                if (decision.guarding) ESP_LOGI(TAG, "Near-end speech confirmed: rms=%.0f correlation=%.2f", decision.rms, decision.correlation);
                vad_state_change_callback_(true);
            } else if (!decision.speech && is_speaking_) {
                is_speaking_ = false;
                vad_state_change_callback_(false);
            }
        }

        if (output_callback_) {
            if (decision.guarding && !decision.speech) {
                if (decision.candidate || decision.pending) {
                    // AFE retains the onset that preceded its speech decision.
                    // Add it once, only for a near-end candidate, not speaker echo.
                    if (speech_preroll_.empty() && decision.candidate &&
                            res->vad_cache && res->vad_cache_size > 0) {
                        speech_preroll_.insert(speech_preroll_.end(), res->vad_cache,
                            res->vad_cache + res->vad_cache_size / sizeof(int16_t));
                    }
                    speech_preroll_.insert(speech_preroll_.end(), res->data, res->data + samples);
                    constexpr size_t max_preroll = 16000 * 400 / 1000;
                    if (speech_preroll_.size() > max_preroll) speech_preroll_.erase(speech_preroll_.begin(), speech_preroll_.end() - max_preroll);
                } else speech_preroll_.clear();
                // Keep the stream clock without sending echo to server VAD/ASR.
                output_buffer_.insert(output_buffer_.end(), samples, 0);
            } else {
                output_buffer_.insert(output_buffer_.end(), speech_preroll_.begin(), speech_preroll_.end());
                speech_preroll_.clear();
                output_buffer_.insert(output_buffer_.end(), res->data, res->data + samples);
            }
            
            // Output complete frames when buffer has enough data
            while (output_buffer_.size() >= frame_samples_) {
                if (output_buffer_.size() == frame_samples_) {
                    // If buffer size equals frame size, move the entire buffer
                    output_callback_(std::move(output_buffer_));
                    output_buffer_.clear();
                    output_buffer_.reserve(frame_samples_);
                } else {
                    // If buffer size exceeds frame size, copy one frame and remove it
                    output_callback_(std::vector<int16_t>(output_buffer_.begin(), output_buffer_.begin() + frame_samples_));
                    output_buffer_.erase(output_buffer_.begin(), output_buffer_.begin() + frame_samples_);
                }
            }
        }
    }
}

void AfeAudioProcessor::EnableDeviceAec(bool enable) {
    if (enable) {
#if CONFIG_USE_DEVICE_AEC
        afe_iface_->enable_vad(afe_data_);
        afe_iface_->enable_aec(afe_data_);
#else
        ESP_LOGE(TAG, "Device AEC is not supported");
#endif
    } else {
        afe_iface_->disable_aec(afe_data_);
        afe_iface_->enable_vad(afe_data_);
    }
}
