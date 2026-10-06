#include "filter.h"

#define FILTER_SAMPLE_SECONDS 0.005f

float Complementary_Filter_x(float angle_m, float gyro_m)
{
    static float angle;
    float K1 = 0.02;
    angle = K1 * angle_m + (1-K1) * (angle + gyro_m * FILTER_SAMPLE_SECONDS);
    return angle;
}

float Complementary_Filter_y(float angle_m, float gyro_m)
{
    static float angle;
    float K1 = 0.02;
    angle = K1 * angle_m + (1-K1) * (angle + gyro_m * FILTER_SAMPLE_SECONDS);
    return angle;
}
