#pragma once
#include <esp_mn_models.h>
#include <esp_mn_speech_commands.h>
#include <esp_timer.h>
#include <esp_log.h>
#include <model_path.h>
#include <nvs.h>
#include <cJSON.h>
#include <mutex>
#include <string>
#include <cstring>
#include <cstdint>
#include "dynamic_wake_gate.h"

// Configuration and inference share one lock: never mutate the command graph
// while detect() is reading it. The original WakeNet remains independent.
class DynamicWakeWord {
public:
    static DynamicWakeWord& Instance() { static DynamicWakeWord instance; return instance; }

    void Initialize(srmodel_list_t* models, int frame_samples) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (data_) return;
        auto name = esp_srmodel_filter(models, ESP_MN_PREFIX, ESP_MN_CHINESE);
        if (!name || strcmp(name, "mn6_cn") != 0) {
            ESP_LOGE("DynamicWakeWord", "mn6_cn missing; flash the new assets partition");
            return;
        }
        iface_ = esp_mn_handle_from_name(name);
        if (!iface_ || !(data_ = iface_->create(name, 3000))) {
            ESP_LOGE("DynamicWakeWord", "Model initialization failed");
            return;
        }
        if (iface_->get_samp_chunksize(data_) != frame_samples || iface_->get_samp_rate(data_) != 16000) {
            iface_->destroy(data_); data_ = nullptr; return;
        }
        if (esp_mn_commands_alloc(iface_, data_) != ESP_OK) {
            iface_->destroy(data_); data_ = nullptr; return;
        }
        iface_->set_det_threshold(data_, kDetectionThreshold);
        frame_samples_ = frame_samples;
        nvs_handle_t handle;
        if (nvs_open("custom_wake", NVS_READONLY, &handle) == ESP_OK) {
            char buffer[256]; size_t size = sizeof(buffer);
            if (nvs_get_str(handle, "config", buffer, &size) == ESP_OK) {
                auto root = cJSON_Parse(buffer);
                auto word = cJSON_GetObjectItem(root, "word");
                auto pinyin = cJSON_GetObjectItem(root, "pinyin");
                if (cJSON_IsString(word) && cJSON_IsString(pinyin) && Valid(word->valuestring, pinyin->valuestring) &&
                    Replace(pinyin->valuestring)) {
                    word_ = word->valuestring; pinyin_ = pinyin->valuestring;
                    persisted_ = true;
                }
                cJSON_Delete(root);
            }
            nvs_close(handle);
        }
        ESP_LOGI("DynamicWakeWord", "Ready, saved word=%s, threshold=%d%%, min_speech_ms=96, revision=2026100504",
            word_.c_str(), static_cast<int>(kDetectionThreshold * 100 + 0.5f));
    }

    std::string Apply(const std::string& word, const std::string& pinyin) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!Valid(word, pinyin)) return "invalid";
        if (!data_) return "not_ready";
        if (persisted_ && word == word_ && pinyin == pinyin_) return "applied";
        // Even a repeated setting is committed before acknowledging, so an
        // initially empty device configuration is confirmed durable too.
        if (!Replace(pinyin)) { Rollback(); return "rejected"; }
        auto root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "word", word.c_str());
        cJSON_AddStringToObject(root, "pinyin", pinyin.c_str());
        char* json = cJSON_PrintUnformatted(root);
        nvs_handle_t handle;
        esp_err_t err = ESP_ERR_NO_MEM;
        if (json) {
            err = nvs_open("custom_wake", NVS_READWRITE, &handle);
            if (err == ESP_OK) {
                // NVS skips writing identical values; commit must succeed.
                err = nvs_set_str(handle, "config", json);
                if (err == ESP_OK) err = nvs_commit(handle);
                nvs_close(handle);
            }
        }
        if (json) cJSON_free(json);
        cJSON_Delete(root);
        if (err != ESP_OK) { Rollback(); return "storage_error"; }
        word_ = word; pinyin_ = pinyin;
        persisted_ = true;
        last_feed_ = 0;
        gate_.Reset();
        ESP_LOGI("DynamicWakeWord", "Applied and saved: %s", word_.c_str());
        return "applied";
    }

    std::string Feed(int16_t* pcm, bool vad_speech) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!data_ || word_.empty()) return {};
        auto now = esp_timer_get_time();
        if (!last_feed_ || now - last_feed_ > 500000) { iface_->clean(data_); gate_.Reset(); }
        last_feed_ = now;
        gate_.Observe(frame_samples_, vad_speech, now);
        auto state = iface_->detect(data_, pcm);
        ++feed_frames_;
        const auto inference_us = esp_timer_get_time() - now;
        if (inference_us > max_inference_us_) max_inference_us_ = inference_us;
        bool hit = false;
        if (state == ESP_MN_STATE_DETECTED) {
            auto result = iface_->get_results(data_);
            if (result && result->num > 0) {
                hit = gate_.Accept(result->command_id[0], result->prob[0], kDetectionThreshold, now);
                // Integer scores remain readable with the configured nano formatter.
                const int probability = std::isfinite(result->prob[0]) && result->prob[0] >= 0 && result->prob[0] <= 1
                    ? static_cast<int>(result->prob[0] * 1000) : -1;
                ESP_LOGI("DynamicWakeWord", "wake_candidate id=%d probability_milli=%d threshold_milli=%d speech_ms=%u hit=%d",
                    result->command_id[0], probability, static_cast<int>(kDetectionThreshold * 1000), gate_.SpeechMs(), hit);
            }
        }
        if (state == ESP_MN_STATE_TIMEOUT) ++timeouts_;
        if (now - last_diagnostic_us_ >= 5000000) {
            int peak = 0;
            for (int i = 0; i < frame_samples_; ++i) {
                int sample = pcm[i]; if (sample < 0) sample = -sample;
                if (sample > peak) peak = sample;
            }
            // The configured newlib-nano formatter does not support %lld;
            // a 64-bit argument here also misaligns the following %s argument.
            const auto max_us = max_inference_us_ > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(max_inference_us_);
            ESP_LOGI("DynamicWakeWord", "wake_health model=mn6_cn frames=%lu timeouts=%lu peak=%d max_us=%lu pinyin=%s",
                (unsigned long)feed_frames_, (unsigned long)timeouts_, peak,
                static_cast<unsigned long>(max_us), pinyin_.c_str());
            last_diagnostic_us_ = now;
            feed_frames_ = timeouts_ = 0;
            max_inference_us_ = 0;
        }
        if (state != ESP_MN_STATE_DETECTING) { iface_->clean(data_); gate_.Reset(); }
        return hit ? word_ : std::string();
    }

    void ResetHistory() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (data_) iface_->clean(data_);
        last_feed_ = 0;
        gate_.Reset();
    }

    void Release() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (data_) {
            esp_mn_commands_free();
            iface_->destroy(data_);
        }
        data_ = nullptr; word_.clear(); pinyin_.clear(); persisted_ = false; last_feed_ = 0;
        gate_.Reset();
    }

private:
    static constexpr float kDetectionThreshold = 0.40f;
    DynamicWakeGate gate_;
    std::mutex mutex_;
    const esp_mn_iface_t* iface_ = nullptr;
    model_iface_data_t* data_ = nullptr;
    std::string word_, pinyin_;
    int64_t last_feed_ = 0;
    int frame_samples_ = 0;
    uint32_t feed_frames_ = 0, timeouts_ = 0;
    int64_t last_diagnostic_us_ = 0, max_inference_us_ = 0;
    bool persisted_ = false;

    void Rollback() {
        if (!Replace(pinyin_)) {
            word_.clear(); pinyin_.clear(); persisted_ = false;
        }
    }

    static bool Valid(const std::string& word, const std::string& pinyin) {
        if (word.empty()) return pinyin.empty();
        if (word.size() < 9 || word.size() > 24 || word.size() % 3 || pinyin.empty() || pinyin.size() > 63) return false;
        for (size_t i = 0; i < word.size(); i += 3) {
            auto a = static_cast<unsigned char>(word[i]);
            auto b = static_cast<unsigned char>(word[i+1]);
            auto c = static_cast<unsigned char>(word[i+2]);
            if ((a & 0xf0) != 0xe0 || (b & 0xc0) != 0x80 || (c & 0xc0) != 0x80) return false;
            auto cp = ((a & 15) << 12) | ((b & 63) << 6) | (c & 63);
            if (cp < 0x4e00 || cp > 0x9fff) return false;
        }
        if (pinyin.front() == ' ' || pinyin.back() == ' ') return false;
        size_t syllables = 1;
        for (size_t i = 0; i < pinyin.size(); ++i) {
            char c = pinyin[i];
            if (c == ' ') { if (i && pinyin[i-1] == ' ') return false; ++syllables; }
            else if (c < 'a' || c > 'z') return false;
        }
        return syllables == word.size() / 3;
    }

    bool Replace(const std::string& pinyin) {
        iface_->clean(data_);
        if (esp_mn_commands_clear() != ESP_OK) return false;
        if (pinyin.empty()) return true; // Feed is disabled for an empty word.
        return esp_mn_commands_add(1, pinyin.c_str()) == ESP_OK && esp_mn_commands_update() == nullptr;
    }
};
