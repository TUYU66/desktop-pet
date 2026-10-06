// Standalone source: silence, short noises and stale speech must not wake.
#include "../main/audio/wake_words/dynamic_wake_gate.h"
#include <cassert>
#include <limits>

int main() {
    DynamicWakeGate gate;
    constexpr int64_t start = 1000000;
    constexpr float threshold = 0.35f;
    gate.Observe(512, false, start);
    assert(!gate.Accept(1, 0.99f, threshold, start));

    gate.Observe(512, true, start + 32000);
    gate.Observe(512, true, start + 64000);
    assert(!gate.Accept(1, 0.99f, threshold, start + 64000));
    gate.Observe(512, true, start + 96000);
    assert(gate.SpeechMs() == 96);
    assert(gate.Accept(1, threshold, threshold, start + 96000));
    assert(!gate.Accept(1, 0.34f, threshold, start + 96000));
    assert(!gate.Accept(2, 0.99f, threshold, start + 96000));
    assert(!gate.Accept(1, std::numeric_limits<float>::quiet_NaN(), threshold, start + 96000));
    assert(!gate.Accept(1, 1.1f, threshold, start + 96000));

    // Recognition can finish after the speaker has stopped, within the tail.
    gate.Observe(512, false, start + 128000);
    assert(gate.Accept(1, 0.8f, threshold, start + 128000));
    assert(!gate.Accept(1, 0.8f, threshold, start + 96000 + DynamicWakeGate::kSpeechTailUs + 1));
    gate.Observe(512, true, start + 96000 + DynamicWakeGate::kSpeechTailUs + 1);
    assert(gate.SpeechMs() == 32); // Speech from an old utterance cannot accumulate.
    assert(!gate.Accept(1, 0.99f, threshold, start + 96000 + DynamicWakeGate::kSpeechTailUs + 1));
    gate.Reset();
    assert(!gate.Accept(1, 0.99f, threshold, start + 1000000));
    return 0;
}
