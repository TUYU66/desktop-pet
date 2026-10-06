/* Host-only regression; no sensor bus or motor execution. */
#include <assert.h>
#include <stdio.h>
#include "../APP/imu_sample.h"

int main(void)
{
    unsigned int word;
    unsigned char bytes[2];
    const unsigned char frame[14] = {
        0x7f, 0xff, 0x80, 0x00, 0xff, 0xff, 0x00, 0x00,
        0x00, 0x01, 0xfe, 0xdc, 0x12, 0x34
    };
    const int expected[7] = {32767, -32768, -1, 0, 1, -292, 4660};
    unsigned int axis;

    /* Cover every bit pattern, especially the negative full-scale boundary
     * which the former >32768 comparison incorrectly treated as positive. */
    for (word = 0; word <= 65535U; ++word) {
        bytes[0] = (unsigned char)(word >> 8);
        bytes[1] = (unsigned char)word;
        assert(Imu_SignedWord(bytes) == (word < 32768U ? (int)word : (int)word - 65536));
    }
    for (axis = 0; axis < 7; ++axis)
        assert(Imu_SignedWord(frame + axis * 2) == expected[axis]);

    puts("IMU signed sample conversion checks passed.");
    return 0;
}
