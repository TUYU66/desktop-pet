// Standalone regression source; no ESP-IDF runtime or microphone needed.
#include "../main/audio/processors/speech_barge_in_gate.h"
#include <cassert>
#include <vector>

static std::vector<int16_t> Signal(uint32_t seed, size_t samples) {
    std::vector<int16_t> result(samples);
    int16_t held = 0;
    for (size_t i = 0; i < samples; ++i) {
        if (i % 8 == 0) {
            seed = seed * 1664525u + 1013904223u;
            held = static_cast<int16_t>((seed >> 16) % 8000 - 4000);
        }
        result[i] = held;
    }
    return result;
}

int main() {
    constexpr int64_t start_us = 1000000;
    constexpr size_t frame = 512; // A 32 ms AFE fetch frame at 16 kHz.
    const auto playback = Signal(7, 16000 * 400 / 1000);

    // Delayed/inverted/scaled speaker echo must not become a near-end voice.
    SpeechBargeInGate echo_gate;
    echo_gate.AddReference(playback.data(), playback.size(), start_us);
    std::vector<int16_t> echo(frame * 12);
    for (size_t i = 0; i < echo.size(); ++i) echo[i] = -playback[i + 64] / 2;
    for (size_t i = 0; i < 12; ++i) {
        const auto decision = echo_gate.Evaluate(echo.data() + i * frame, frame, true, start_us + i * 32000);
        assert(!decision.speech);
        if (i > 0) assert(decision.correlation > 0.9f);
    }

    // Independent sustained speech can interrupt playback; one VAD pulse cannot.
    SpeechBargeInGate near_gate;
    near_gate.AddReference(playback.data(), playback.size(), start_us);
    const auto user = Signal(99, frame * 12);
    auto first = near_gate.Evaluate(user.data(), frame, true, start_us);
    assert(first.candidate && !first.speech);
    SpeechBargeInGate::Decision decision;
    for (size_t i = 1; i < 12; ++i) {
        decision = near_gate.Evaluate(user.data() + i * frame, frame, true, start_us + i * 32000);
    }
    assert(decision.speech);

    // A brief near-end correction should be confirmed within four 32ms frames,
    // including when spoken softly. Correlated speaker echo above is still denied.
    SpeechBargeInGate short_gate;
    short_gate.AddReference(playback.data(), playback.size(), start_us);
    auto quiet_user = user;
    for (auto& sample : quiet_user) sample /= 8;
    for (size_t i = 0; i < 4; ++i) {
        decision = short_gate.Evaluate(quiet_user.data() + i * frame, frame, true,
            start_us + i * 32000);
    }
    assert(decision.guarding && decision.speech);

    // A continuously fed but silent hardware reference is ordinary quiet
    // listening, not playback. It must not gate short/quiet user speech.
    SpeechBargeInGate silent_reference_gate;
    std::vector<int16_t> silent_reference(frame, 0);
    for (size_t i = 0; i < 20; ++i) {
        silent_reference_gate.AddReference(silent_reference.data(), frame, start_us + i * 32000);
        decision = silent_reference_gate.Evaluate(quiet_user.data(), frame, true,
            start_us + i * 32000);
        assert(!decision.guarding && decision.speech);
    }

    // A single lost VAD frame inside a short phrase must not discard its prefix.
    SpeechBargeInGate gap_gate;
    gap_gate.AddReference(playback.data(), playback.size(), start_us);
    for (size_t i = 0; i < 9; ++i) {
        decision = gap_gate.Evaluate(user.data() + i * frame, frame, i != 3, start_us + i * 32000);
    }
    assert(decision.speech);

    SpeechBargeInGate pulse_gate;
    pulse_gate.AddReference(playback.data(), playback.size(), start_us);
    pulse_gate.Evaluate(user.data(), frame, true, start_us);
    std::vector<int16_t> silence(frame, 0);
    decision = pulse_gate.Evaluate(silence.data(), frame, false, start_us + 32000);
    assert(!decision.speech && !decision.candidate);

    // DC/low residual levels are not near-end speech during playback.
    std::vector<int16_t> dc(frame, 5000);
    decision = pulse_gate.Evaluate(dc.data(), frame, true, start_us + 64000);
    assert(decision.rms == 0 && !decision.candidate);

    // After playback tail expires, quiet listening keeps its ordinary VAD path.
    decision = near_gate.Evaluate(user.data(), frame, true, start_us + 600000);
    assert(!decision.guarding && decision.speech);
    near_gate.Reset();
    decision = near_gate.Evaluate(user.data(), frame, true, start_us + 700000);
    assert(!decision.guarding && decision.speech);
    return 0;
}
