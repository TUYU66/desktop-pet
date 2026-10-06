#include <assert.h>
#include "../APP/motor_mix.h"

static const MotorThresholds left_cal = {
    MOTOR_LEFT_FORWARD_START_PWM, MOTOR_LEFT_FORWARD_RUN_PWM,
    MOTOR_LEFT_REVERSE_START_PWM, MOTOR_LEFT_REVERSE_RUN_PWM
};
static const MotorThresholds right_cal = {
    MOTOR_RIGHT_FORWARD_START_PWM, MOTOR_RIGHT_FORWARD_RUN_PWM,
    MOTOR_RIGHT_REVERSE_START_PWM, MOTOR_RIGHT_REVERSE_RUN_PWM
};

int main(void)
{
    MotorCompensationState left = {0}, right = {0};
    MotorCompensationState state = {0};
    MotorThresholds invalid = {1000, 1400, 1520, 1400};
    uint32_t t;
    const int assist_demand = MOTOR_COMP_BLEND_PWM;
    const int left_running_output = MOTOR_LEFT_FORWARD_RUN_PWM + assist_demand;
    const int reverse_step_limit = assist_demand + 5 * MOTOR_COMP_RISE_PWM_PER_MS;
    int l, r, output = 0, previous = 0;

    /* A passive gyro correction cannot become a counter-rotating start kick. */
    for (t = 0; t <= 1000; t += 5) {
        Motor_MixMeasured(0, 2, 0, 0, 0, t, &left_cal, &right_cal, &left, &right, 2600, &l, &r);
        assert(l == 2 && r == -2);
        assert(!left.assist_active && !right.assist_active);
    }

    /* Small noisy common drive does not command the full measured threshold.
     * Zero removes torque and the raw controller demand is not filtered away. */
    Motor_ResetCompensationState(&state);
    assert(Motor_CompensateMeasured(1, 0, 0, &left_cal, &state, 2600) == 1);
    assert(Motor_CompensateMeasured(1, 0, 5, &left_cal, &state, 2600) ==
           1 + MOTOR_LEFT_FORWARD_RUN_PWM / MOTOR_COMP_BLEND_PWM);
    assert(!state.assist_active);
    assert(Motor_CompensateMeasured(0, 0, 10, &left_cal, &state, 2600) == 0);
    assert(state.offset == 0);

    /* Sustained small corrections remain below the measured running floor on
     * both wheels and in both directions, instead of arming a start kick. */
    assert(MOTOR_COMP_BLEND_PWM < left_cal.forward_start - left_cal.forward_run);
    assert(MOTOR_COMP_BLEND_PWM < right_cal.forward_start - right_cal.forward_run);
    {
        int direction;
        for (direction = -1; direction <= 1; direction += 2) {
            Motor_ResetCompensationState(&left); Motor_ResetCompensationState(&right);
            for (t = 0; t <= 500; t += 5) {
                Motor_MixMeasured(direction * 40, 0, 0, 0, 0, t,
                                  &left_cal, &right_cal, &left, &right, 2600, &l, &r);
                assert(!left.assist_active && !right.assist_active);
                assert(l * direction > 0 && l * direction < left_cal.forward_run);
                assert(r * direction > 0 && r * direction < right_cal.forward_run);
            }
            assert(Motor_CompensateMeasured(0, 0, 505, &left_cal, &left, 2600) == 0);
            assert(Motor_CompensateMeasured(0, 0, 505, &right_cal, &right, 2600) == 0);
        }
    }

    /* The wider blend must not remove the narrower right-wheel start window. */
    Motor_ResetCompensationState(&state);
    for (t = 0; t <= 50; t += 5)
        output = Motor_CompensateMeasured(assist_demand, 0, t, &right_cal, &state, 2600);
    assert(output == right_cal.forward_start && state.assist_active);
    for (t = 55; t <= 110; t += 5)
        output = Motor_CompensateMeasured(assist_demand, 0, t, &right_cal, &state, 2600);
    assert(output == right_cal.forward_run + assist_demand && !state.assist_active && state.assist_used);

    /* A stationary wheel gets one short floor assist, not an endless kick.
     * The friction offset increases no faster than 200 per 5 ms sample. */
    Motor_ResetCompensationState(&state);
    for (t = 0; t <= 250; t += 5) {
        output = Motor_CompensateMeasured(assist_demand, 0, t, &left_cal, &state, 2600);
        assert(output - previous <= 200 || t == 0);
        assert(output <= left_cal.forward_start);
        if (t >= 50 && t < 110) assert(output == left_cal.forward_start && state.assist_active);
        if (t >= 110) assert(output == left_running_output && !state.assist_active && state.assist_used);
        previous = output;
    }
    /* An encoder pulse ends assist immediately; short pulse gaps do not boost. */
    assert(Motor_CompensateMeasured(assist_demand, 1, 255, &left_cal, &state, 2600) == left_running_output);
    assert(!state.assist_used);
    assert(Motor_CompensateMeasured(assist_demand, 0, 280, &left_cal, &state, 2600) == assist_demand); /* >20 ms gap resets history */

    Motor_ResetCompensationState(&state);
    for (t = 0; t <= 50; t += 5) Motor_CompensateMeasured(assist_demand, 0, t, &left_cal, &state, 2600);
    assert(state.assist_active);
    assert(Motor_CompensateMeasured(assist_demand, 1, 55, &left_cal, &state, 2600) == left_running_output);
    for (t = 60; t < 100; t += 5) {
        assert(Motor_CompensateMeasured(assist_demand, 0, t, &left_cal, &state, 2600) == left_running_output);
    }
    /* Larger PD/PI demand must cancel a floor assist rather than produce a
     * negative friction offset or hold output at only the start threshold. */
    Motor_ResetCompensationState(&state);
    for (t = 0; t <= 50; t += 5) Motor_CompensateMeasured(assist_demand, 0, t, &left_cal, &state, 2600);
    assert(Motor_CompensateMeasured(1700, 0, 55, &left_cal, &state, 2600) == 2600);
    assert(!state.assist_active && state.offset >= 0);

    /* Rapid reversals cannot reuse the previous sign's torque or continuously
     * rearm a failed assist. The new offset is ramped, not a full start impulse. */
    Motor_ResetCompensationState(&state);
    for (t = 0; t <= 110; t += 5) Motor_CompensateMeasured(assist_demand, 0, t, &left_cal, &state, 2600);
    for (t = 115; t <= 400; t += 5) {
        int demand = (t / 5) % 2 ? -assist_demand : assist_demand;
        output = Motor_CompensateMeasured(demand, 0, t, &left_cal, &state, 2600);
        assert(output * demand > 0 && output <= reverse_step_limit && output >= -reverse_step_limit);
        assert(!state.assist_active && state.assist_used);
    }
    /* A genuine neutral interval may rearm an attempt, but cooldown still
     * prevents another assist immediately after a short stop/restart. */
    Motor_ResetCompensationState(&state);
    for (t = 0; t <= 110; t += 5) Motor_CompensateMeasured(assist_demand, 0, t, &left_cal, &state, 2600);
    for (t = 115; t <= 220; t += 5) assert(Motor_CompensateMeasured(0, 0, t, &left_cal, &state, 2600) == 0);
    assert(!state.assist_used);
    for (t = 225; t < 300; t += 5) {
        Motor_CompensateMeasured(assist_demand, 0, t, &left_cal, &state, 2600);
        assert(!state.assist_active);
    }
    assert(Motor_CompensateMeasured(assist_demand, 0, 300, &left_cal, &state, 2600) == left_cal.forward_start);

    /* Movement-based steady offsets distinguish wheels and directions. */
    Motor_ResetCompensationState(&left); Motor_ResetCompensationState(&right);
    for (t = 0; t <= 50; t += 5) {
        Motor_MixMeasured(100, 0, 0, 1, 1, t, &left_cal, &right_cal, &left, &right, 2600, &l, &r);
    }
    assert(l == 1480 && r == 1520);
    Motor_ResetCompensationState(&left); Motor_ResetCompensationState(&right);
    for (t = 0; t <= 50; t += 5) {
        Motor_MixMeasured(0, 180, 1, 1, -1, t, &left_cal, &right_cal, &left, &right, 2600, &l, &r);
    }
    assert(l == 1560 && r == -1600);
    Motor_ResetCompensationState(&left); Motor_ResetCompensationState(&right);
    for (t = 0; t <= 50; t += 5) {
        Motor_MixMeasured(0, -180, 1, -1, 1, t, &left_cal, &right_cal, &left, &right, 2600, &l, &r);
    }
    assert(l == -1560 && r == 1600);
    for (t = 55; t <= 100; t += 5) {
        Motor_MixMeasured(2000, 1000, 1, 1, 1, t, &left_cal, &right_cal, &left, &right, 2600, &l, &r);
        assert(l <= 2600 && l >= -2600 && r <= 2600 && r >= -2600);
    }

    /* Millisecond wrap is supported. A long gap clears a latent assist. */
    Motor_ResetCompensationState(&state);
    for (t = 0; t <= 50; t += 5) {
        output = Motor_CompensateMeasured(assist_demand, 0, (uint32_t)(UINT32_MAX - 20U + t), &left_cal, &state, 2600);
    }
    assert(output == left_cal.forward_start && state.assist_active);
    assert(Motor_CompensateMeasured(assist_demand, 0, 500, &left_cal, &state, 2600) == assist_demand);
    assert(!state.assist_active);
    assert(Motor_CompensateMeasured(40, 0, 505, &invalid, &state, 2600) == 40);
    assert(!state.initialized);
    return 0;
}
