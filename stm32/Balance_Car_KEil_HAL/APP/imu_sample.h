#ifndef IMU_SAMPLE_H
#define IMU_SAMPLE_H

/* MPU6050 sensor registers are big-endian signed 16-bit words. Keep the
 * conversion explicit, including 0x8000, without signed narrowing casts. */
static inline int Imu_SignedWord(const unsigned char *bytes)
{
    int value = (int)bytes[0] * 256 + bytes[1];
    return value >= 32768 ? value - 65536 : value;
}

#endif
