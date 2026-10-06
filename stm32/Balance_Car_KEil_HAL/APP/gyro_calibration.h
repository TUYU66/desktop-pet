#ifndef GYRO_CALIBRATION_H
#define GYRO_CALIBRATION_H

#include <stdint.h>

#define GYRO_CAL_MAX_GAP_MS 50U
#define GYRO_CAL_NOISE_VARIANCE 64.0f

/* Manual, RAM-only calibration. Units match the signed control gyro inputs.
 * Never learn bias while balancing or silently refresh a failed window. */
typedef struct {
    unsigned int state, error, samples, valid, revision;
    uint32_t started, last;
    float angle_start, pitch_start, yaw_start;
    float pitch_mean, yaw_mean, pitch_m2, yaw_m2;
    float pitch_bias, yaw_bias;
} GyroCalibration;

static inline void GyroCal_Fail(volatile GyroCalibration *s, unsigned int reason)
{
    if (s->state == 1) { s->state = 3; s->error = reason; }
}

static inline void GyroCal_Begin(volatile GyroCalibration *s, uint32_t now,
                                 float angle, float pitch, float yaw)
{
    s->state = 1; s->error = s->samples = 0;
    s->started = s->last = now;
    s->angle_start = angle; s->pitch_start = pitch; s->yaw_start = yaw;
    s->pitch_mean = s->yaw_mean = s->pitch_m2 = s->yaw_m2 = 0;
}

static inline void GyroCal_Clear(volatile GyroCalibration *s)
{
    s->state = s->error = s->samples = s->valid = 0;
    s->pitch_bias = s->yaw_bias = 0;
    s->revision = s->revision >= 2147483646U ? 1 : s->revision + 1;
}

static inline void GyroCal_Observe(volatile GyroCalibration *s, uint32_t now,
                                   float angle, float pitch, float yaw, int quiet)
{
    float delta;
    uint32_t elapsed;
    if (s->state != 1) return;
    if (!quiet) { GyroCal_Fail(s, 1); return; }
    /* Isolated bounded sensor spikes are included in the window statistics.
     * Reject actual tilt/fast rotation immediately; judge noise over 3 seconds,
     * never require one raw or corrected reading to remain constant or zero. */
    if (!(angle > s->angle_start - .3f && angle < s->angle_start + .3f) ||
        !(pitch > -164 && pitch < 164 && yaw > -164 && yaw < 164)) {
        GyroCal_Fail(s, 2); return;
    }
    if ((uint32_t)(now - s->last) > GYRO_CAL_MAX_GAP_MS) { GyroCal_Fail(s, 3); return; }
    if (now == s->last) return;
    s->last = now;
    elapsed = (uint32_t)(now - s->started);
    if (elapsed > 5000U || s->samples >= 1000) { GyroCal_Fail(s, 4); return; }
    s->samples++;
    delta = pitch - s->pitch_mean; s->pitch_mean += delta / s->samples;
    s->pitch_m2 += delta * (pitch - s->pitch_mean);
    delta = yaw - s->yaw_mean; s->yaw_mean += delta / s->samples;
    s->yaw_m2 += delta * (yaw - s->yaw_mean);
    if (elapsed < 3000U || s->samples < 500) return;
    if (!(s->pitch_m2 / (s->samples - 1) <= GYRO_CAL_NOISE_VARIANCE &&
          s->yaw_m2 / (s->samples - 1) <= GYRO_CAL_NOISE_VARIANCE)) {
        GyroCal_Fail(s, 2); return;
    }
    if (!(s->pitch_mean >= -82 && s->pitch_mean <= 82 && s->yaw_mean >= -82 && s->yaw_mean <= 82)) {
        GyroCal_Fail(s, 5); return;
    }
    s->pitch_bias = s->pitch_mean; s->yaw_bias = s->yaw_mean;
    s->valid = 1; s->state = 2; s->error = 0;
    s->revision = s->revision >= 2147483646U ? 1 : s->revision + 1;
}

#endif
