#include "../main/protocols/binary_audio_frame.h"
#include <cassert>
#include <cstring>
#include <cstdio>

int main() {
    BinaryAudioFrame frame;
    const uint8_t v2[] = {0,2,0,0,0,0,0,0,0,0,1,2,0,0,0,3,11,22,33};
    const uint8_t v3[] = {0,0,0,3,11,22,33};
    assert(ParseBinaryAudioFrame(2, v2, sizeof(v2), frame));
    assert(frame.timestamp == 258 && frame.size == 3 && frame.payload[2] == 33);
    assert(ParseBinaryAudioFrame(3, v3, sizeof(v3), frame));
    assert(frame.timestamp == 0 && frame.size == 3);
    assert(ParseBinaryAudioFrame(1, v3, sizeof(v3), frame));
    assert(frame.payload == v3 && frame.size == sizeof(v3));
    for (size_t size = 0; size < sizeof(v2); ++size)
        assert(!ParseBinaryAudioFrame(2, v2, size, frame));
    for (size_t size = 0; size < sizeof(v3); ++size)
        assert(!ParseBinaryAudioFrame(3, v3, size, frame));
    uint8_t buffer[sizeof(v2) + 2] = {};
    memcpy(buffer + 1, v2, sizeof(v2));
    assert(ParseBinaryAudioFrame(2, buffer + 1, sizeof(v2), frame));
    assert(memcmp(buffer + 1, v2, sizeof(v2)) == 0);
    buffer[13] = 255; // Oversized declared payload must not allocate or read it.
    assert(!ParseBinaryAudioFrame(2, buffer + 1, sizeof(v2), frame));
    assert(frame.payload == nullptr && frame.size == 0);
    memcpy(buffer, v2, sizeof(v2));
    buffer[3] = 1; // Non-audio message.
    assert(!ParseBinaryAudioFrame(2, buffer, sizeof(v2), frame));
    assert(!ParseBinaryAudioFrame(2, nullptr, sizeof(v2), frame));
    assert(!ParseBinaryAudioFrame(3, v3, sizeof(v3) + 1, frame));
    puts("binary_audio_frame_test passed");
}
