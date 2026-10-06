#ifndef CALIBRATION_DEADLINE_H
#define CALIBRATION_DEADLINE_H

#include <stdint.h>

#define CALIBRATION_LEASE_MS 1500U
#define CALIBRATION_RUN_MS 8000U
#define CALIBRATION_IDLE_MS 120000U

/* Tool integer properties carry the low 31 bits. A lease is much shorter than
 * half that period, so old/future samples cannot be mistaken for fresh ones. */
static inline uint32_t Calibration_SampleAge(uint32_t now, uint32_t sample)
{
    return (now - sample) & 0x7FFFFFFFU;
}

static inline int Calibration_SampleFresh(uint32_t now, uint32_t sample)
{
    return Calibration_SampleAge(now, sample) < CALIBRATION_LEASE_MS;
}

static inline uint32_t Calibration_LeaseStart(uint32_t now, uint32_t sample)
{
    return now - Calibration_SampleAge(now, sample);
}

#endif
