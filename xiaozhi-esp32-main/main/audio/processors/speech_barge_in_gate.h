#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <deque>
#include <mutex>

// VAD alone also detects loudspeaker speech. Confirm sustained near-end speech
// using the post-AEC samples and a bounded history of actual playback samples.
class SpeechBargeInGate {
public:
    struct Decision {
        bool speech = false;
        bool candidate = false;
        bool guarding = false;
        bool pending = false;
        float correlation = 0;
        float rms = 0;
    };

    void AddReference(const int16_t* data, size_t samples, int64_t now_us, size_t stride = 1) {
        std::lock_guard<std::mutex> lock(reference_mutex_);
        if (now_us - last_reference_us_ > kTailUs) reference_.clear();
        // Silent reference frames (including a hardware R channel in quiet
        // listening) must not keep the playback guard active indefinitely.
        double sum = 0, energy = 0;
        for (size_t i = 0; i < samples; ++i) {
            const double sample = data[i * stride];
            sum += sample;
            energy += sample * sample;
        }
        if (samples > 0 && energy / samples - (sum / samples) * (sum / samples) >= 400.0) {
            last_reference_us_ = now_us;
        }
        for (size_t i = 0; i + kDecimation <= samples; i += kDecimation) {
            float sum = 0;
            for (size_t j = 0; j < kDecimation; ++j) sum += data[(i + j) * stride];
            reference_.push_back(sum / kDecimation);
            if (reference_.size() > kReferenceSamples) reference_.pop_front();
        }
    }

    Decision Evaluate(const int16_t* data, size_t samples, bool vad_speech, int64_t now_us) {
        std::lock_guard<std::mutex> lock(mutex_);
        Decision decision;
        size_t reference_count = 0;
        {
            // Playback never waits for the correlation scan, only this bounded copy.
            std::lock_guard<std::mutex> reference_lock(reference_mutex_);
            decision.guarding = last_reference_us_ > 0 && now_us - last_reference_us_ < kTailUs;
            reference_count = reference_.size();
            std::copy(reference_.begin(), reference_.end(), reference_scratch_.begin());
        }
        float sum = 0, energy = 0;
        for (size_t i = 0; i < samples; ++i) sum += data[i];
        if (samples == 0) return decision;
        const float mean = sum / samples;
        for (size_t i = 0; i < samples; ++i) {
            const float value = data[i] - mean;
            energy += value * value;
        }
        decision.rms = std::sqrt(energy / samples);
        if (!decision.guarding) {
            if (!vad_speech) noise_rms_ = 0.98f * noise_rms_ + 0.02f * std::min(decision.rms, 300.0f);
            observed_.clear();
            candidate_samples_ = 0;
            gap_samples_ = 0;
            confirmed_ = false;
            decision.speech = vad_speech;
            return decision;
        }
        for (size_t i = 0; i + kDecimation <= samples; i += kDecimation) {
            float value = 0;
            for (size_t j = 0; j < kDecimation; ++j) value += data[i + j];
            observed_.push_back(value / kDecimation);
            if (observed_.size() > kWindowSamples) observed_.pop_front();
        }
        const float minimum_rms = std::max(60.0f, noise_rms_ * 2.5f);
        if (vad_speech && decision.rms >= minimum_rms) decision.correlation = EchoCorrelation(reference_count);
        decision.candidate = vad_speech && decision.rms >= minimum_rms && decision.correlation < 0.72f;
        if (decision.candidate && observed_.size() == kWindowSamples) {
            candidate_samples_ += samples;
            gap_samples_ = 0;
            const size_t required = decision.correlation < 0.45f && decision.rms >= minimum_rms * 1.5f
                ? 16000 * 96 / 1000 : 16000 * 160 / 1000;
            if (candidate_samples_ >= required) confirmed_ = true;
        } else {
            gap_samples_ += samples;
            if (gap_samples_ > 16000 * (confirmed_ ? 128 : 64) / 1000) {
                candidate_samples_ = 0;
                confirmed_ = false;
            }
        }
        decision.pending = candidate_samples_ > 0;
        decision.speech = confirmed_;
        return decision;
    }

    void Reset() {
        std::lock_guard<std::mutex> lock(mutex_);
        std::lock_guard<std::mutex> reference_lock(reference_mutex_);
        reference_.clear();
        observed_.clear();
        last_reference_us_ = 0;
        candidate_samples_ = 0;
        gap_samples_ = 0;
        confirmed_ = false;
        noise_rms_ = 20;
    }

private:
    static constexpr size_t kDecimation = 8;
    static constexpr size_t kWindowSamples = 16000 * 64 / 1000 / kDecimation;
    static constexpr size_t kReferenceSamples = 16000 * 800 / 1000 / kDecimation;
    static constexpr int64_t kTailUs = 500000;
    std::mutex mutex_;
    std::mutex reference_mutex_;
    std::deque<float> reference_;
    std::deque<float> observed_;
    size_t candidate_samples_ = 0;
    size_t gap_samples_ = 0;
    bool confirmed_ = false;
    int64_t last_reference_us_ = 0;
    float noise_rms_ = 20;

    std::array<float, kWindowSamples> target_scratch_{};
    std::array<float, kReferenceSamples> reference_scratch_{};
    std::array<float, kReferenceSamples + 1> sums_{};
    std::array<float, kReferenceSamples + 1> energies_{};

    float EchoCorrelation(size_t reference_count) {
        if (observed_.size() != kWindowSamples || reference_count < kWindowSamples) return 0;
        // Zero-mean, normalized correlation tolerates speaker gain and delay.
        // 2 kHz / 64 ms keeps the delay scan bounded on the audio task.
        auto& target = target_scratch_;
        auto& reference = reference_scratch_;
        std::copy(observed_.begin(), observed_.end(), target.begin());
        float mean = 0;
        for (auto sample : target) mean += sample;
        mean /= target.size();
        float target_energy = 0;
        for (auto& sample : target) {
            sample -= mean;
            target_energy += sample * sample;
        }
        if (target_energy < 1) return 0;
        auto& sums = sums_;
        auto& energies = energies_;
        sums[0] = 0;
        energies[0] = 0;
        for (size_t i = 0; i < reference_count; ++i) {
            sums[i + 1] = sums[i] + reference[i];
            energies[i + 1] = energies[i] + reference[i] * reference[i];
        }
        float best = 0;
        for (size_t offset = 0; offset + target.size() <= reference_count; ++offset) {
            const auto end = offset + target.size();
            const float total = sums[end] - sums[offset];
            const float ref_energy = energies[end] - energies[offset] - total * total / target.size();
            if (ref_energy < 1) continue;
            float product = 0;
            for (size_t j = 0; j < target.size(); ++j) product += target[j] * reference[offset + j];
            const float score = product * product / (target_energy * ref_energy);
            best = std::max(best, score);
        }
        return std::sqrt(std::min(1.0f, best));
    }
};
