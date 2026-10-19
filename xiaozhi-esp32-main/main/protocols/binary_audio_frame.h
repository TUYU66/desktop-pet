#pragma once
#include <cstddef>
#include <cstdint>

struct BinaryAudioFrame {
    const uint8_t* payload = nullptr;
    size_t size = 0;
    uint32_t timestamp = 0;
};

// The received buffer may be unaligned and must remain read-only.
inline bool ParseBinaryAudioFrame(int version, const uint8_t* data, size_t length,
                                 BinaryAudioFrame& frame) {
    frame = {};
    if (!data || length == 0) return false;
    const auto read16 = [](const uint8_t* p) -> uint16_t {
        return (uint16_t(p[0]) << 8) | uint16_t(p[1]);
    };
    const auto read32 = [](const uint8_t* p) -> uint32_t {
        return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) |
               (uint32_t(p[2]) << 8) | uint32_t(p[3]);
    };
    size_t header = 0;
    uint32_t timestamp = 0;
    size_t payload_size = length;
    if (version == 2) {
        header = 16;
        if (length < header || read16(data) != 2 || read16(data + 2) != 0) return false;
        timestamp = read32(data + 8);
        payload_size = read32(data + 12);
    } else if (version == 3) {
        header = 4;
        if (length < header || data[0] != 0) return false;
        payload_size = read16(data + 2);
    }
    if (payload_size == 0 || payload_size != length - header) return false;
    frame.payload = data + header;
    frame.size = payload_size;
    frame.timestamp = timestamp;
    return true;
}
