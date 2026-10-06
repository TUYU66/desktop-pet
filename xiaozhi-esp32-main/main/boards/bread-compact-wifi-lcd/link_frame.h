#ifndef XIAOZHI_LINK_FRAME_H
#define XIAOZHI_LINK_FRAME_H

#include <stddef.h>
#include <stdint.h>

/* Wire v2: @payload*HHHH\n. CRC-16/CCITT-FALSE over payload only.
 * Keep identical to the ESP32 link_frame.h. No payload may contain @, *, CR or LF.
 * Check vector: "123456789" -> 0x29B1. */
static uint16_t link_crc16(const char *data, size_t length)
{
    uint16_t crc = 0xffffU;
    size_t i;
    unsigned int bit;
    for (i = 0; i < length; ++i) {
        crc ^= (uint16_t)((uint8_t)data[i] << 8);
        for (bit = 0; bit < 8; ++bit)
            crc = (uint16_t)((crc & 0x8000U) ? (crc << 1) ^ 0x1021U : crc << 1);
    }
    return crc;
}

/* 1=valid, 0=framing error, -1=CRC mismatch. Caller consumed @. */
static int link_decode(char *data, size_t length)
{
    uint16_t expected = 0;
    size_t i, payload_length;
    if (length < 6 || data[length - 5] != '*') return 0;
    payload_length = length - 5;
    for (i = 0; i < payload_length; ++i)
        if (data[i] < 32 || data[i] > 126 || data[i] == '@' || data[i] == '*') return 0;
    for (i = length - 4; i < length; ++i) {
        unsigned int digit;
        if (data[i] >= '0' && data[i] <= '9') digit = (unsigned int)(data[i] - '0');
        else if (data[i] >= 'A' && data[i] <= 'F') digit = (unsigned int)(data[i] - 'A' + 10);
        else return 0;
        expected = (uint16_t)((expected << 4) | digit);
    }
    if (expected != link_crc16(data, payload_length)) return -1;
    data[payload_length] = '\0';
    return 1;
}

#endif
