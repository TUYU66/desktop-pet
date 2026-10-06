#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

// MultiNet runs continuously to retain the beginning of a quiet keyword.
// A result from silence alone must never wake the device.
class DynamicWakeGate {
public:
    static constexpr uint32_t kMinimumSpeechSamples = 16000 * 96 / 1000;
    static constexpr int64_t kSpeechTailUs = 500000;

    void Observe(size_t samples, bool speech, int64_t now_us) {
        if (!last_speech_us_ || now_us - last_speech_us_ > kSpeechTailUs) Reset();
        if (speech) {
            last_speech_us_ = now_us;
            speech_samples_ = std::min(kMinimumSpeechSamples,
                speech_samples_ + static_cast<uint32_t>(std::min<size_t>(samples, kMinimumSpeechSamples)));
        }
    }

    bool Accept(int command, float probability, float threshold, int64_t now_us) const {
        return command == 1 && std::isfinite(probability) && probability >= threshold && probability <= 1.0f &&
            last_speech_us_ > 0 && now_us >= last_speech_us_ && now_us - last_speech_us_ <= kSpeechTailUs &&
            speech_samples_ >= kMinimumSpeechSamples;
    }

    unsigned SpeechMs() const { return speech_samples_ / 16; }
    void Reset() { speech_samples_ = 0; last_speech_us_ = 0; }

private:
    uint32_t speech_samples_ = 0;
    int64_t last_speech_us_ = 0;
};
