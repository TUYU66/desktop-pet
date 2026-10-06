#ifndef MOTOR_MIX_H
#define MOTOR_MIX_H

#include <stdint.h>
#include "motor_compensation_config.h"

typedef struct {
    int forward_start, forward_run, reverse_start, reverse_run;
} MotorThresholds;

typedef struct {
    int initialized, direction, offset;
    int assist_active, assist_used, assist_seen, neutral_tracking;
    uint32_t last_tick, last_motion_tick, demand_since;
    uint32_t assist_since, neutral_since;
} MotorCompensationState;

static inline void Motor_ResetCompensationState(MotorCompensationState *state)
{
    MotorCompensationState empty = {0};
    *state = empty;
}

/* Pure PWM arithmetic, shared by the hardware driver and host checks. */
static inline int Motor_CompensateDeadzone(int pulse, int deadzone)
{
    if (pulse > 0) return pulse + deadzone;
    if (pulse < 0) return pulse - deadzone;
    return 0;
}

static inline int Motor_ClampPwm(int pulse, int limit)
{
    if (pulse > limit) return limit;
    if (pulse < -limit) return -limit;
    return pulse;
}

/* Encoder pulses are used only as evidence of movement, not an assumed
 * forward/backward convention or proof that a loaded wheel has started. */
static inline int Motor_CompensateMeasured(int pulse, int encoder, uint32_t now,
                                          const MotorThresholds *thresholds,
                                          MotorCompensationState *state, int limit)
{
    uint32_t dt = state->initialized ? (uint32_t)(now - state->last_tick) : 0U;
    int direction, magnitude, start, run, target, rise;
    if (!state->initialized || dt > 20U) {
        /* No old assist survives a stopped controller or a lost sample run. */
        Motor_ResetCompensationState(state);
        state->initialized = 1;
        state->last_motion_tick = state->demand_since = now;
        dt = 0U;
    }
    state->last_tick = now;
    pulse = Motor_ClampPwm(pulse, limit);
    if (encoder != 0) {
        state->last_motion_tick = now;
        state->assist_active = state->assist_used = 0;
    }
    if (pulse == 0) {
        state->direction = state->offset = state->assist_active = 0;
        if (!state->neutral_tracking) {
            state->neutral_tracking = 1;
            state->neutral_since = now;
        } else if ((uint32_t)(now - state->neutral_since) >= MOTOR_COMP_NEUTRAL_MS) {
            state->assist_used = 0;
        }
        return 0; /* Zero demand never retains friction torque. */
    }
    state->neutral_tracking = 0;
    direction = pulse > 0 ? 1 : -1;
    magnitude = pulse > 0 ? pulse : -pulse;
    start = direction > 0 ? thresholds->forward_start : thresholds->reverse_start;
    run = direction > 0 ? thresholds->forward_run : thresholds->reverse_run;
    if (run <= 0 || start < run || start > limit) {
        Motor_ResetCompensationState(state);
        return pulse; /* Invalid settings do not invent a motor offset. */
    }
    if (direction != state->direction) {
        /* Never keep the old sign's compensation as a reverse command arrives.
         * A sign change alone does not rearm a failed start attempt. */
        state->direction = direction;
        state->offset = state->assist_active = 0;
        state->demand_since = now;
    }
    target = magnitude < MOTOR_COMP_BLEND_PWM
        ? run * magnitude / MOTOR_COMP_BLEND_PWM : run;
    if (state->assist_active && ((uint32_t)(now - state->assist_since) >= MOTOR_COMP_ASSIST_MS ||
                                magnitude < MOTOR_COMP_BLEND_PWM || magnitude + run >= start)) {
        state->assist_active = 0;
    }
    if (!state->assist_active && !state->assist_used && magnitude >= MOTOR_COMP_BLEND_PWM &&
        magnitude + run < start &&
        (uint32_t)(now - state->last_motion_tick) >= MOTOR_COMP_QUIET_MS &&
        (uint32_t)(now - state->demand_since) >= MOTOR_COMP_DEMAND_MS &&
        (!state->assist_seen || (uint32_t)(now - state->assist_since) >= MOTOR_COMP_COOLDOWN_MS)) {
        state->assist_active = state->assist_used = state->assist_seen = 1;
        state->assist_since = now;
    }
    if (state->assist_active) target = start - magnitude;
    /* Only the added offset ramps up. Removing it is immediate, so a declining
     * or zero command cannot leave a large stale torque. Unmodified controller
     * demand remains direct and both combined outputs retain the PWM limit. */
    rise = (int)(dt > 10U ? 10U : dt) * MOTOR_COMP_RISE_PWM_PER_MS;
    if (state->offset > target) state->offset = target;
    else if (target - state->offset > rise) state->offset += rise;
    else state->offset = target;
    return Motor_ClampPwm(pulse + direction * state->offset, limit);
}

static inline void Motor_MixMeasured(int common, int yaw, int commanded_turn,
                                     int encoder_left, int encoder_right, uint32_t now,
                                     const MotorThresholds *left_thresholds,
                                     const MotorThresholds *right_thresholds,
                                     MotorCompensationState *left_state,
                                     MotorCompensationState *right_state,
                                     int limit, int *left, int *right)
{
    if (commanded_turn) {
        *left = Motor_CompensateMeasured(common + yaw, encoder_left, now, left_thresholds, left_state, limit);
        *right = Motor_CompensateMeasured(common - yaw, encoder_right, now, right_thresholds, right_state, limit);
    } else {
        /* Keep small passive yaw corrections outside the friction compensation.
         * Raw gyro noise at common=0 must not start an opposing-wheel kick. */
        *left = Motor_CompensateMeasured(common, encoder_left, now, left_thresholds, left_state, limit) + yaw;
        *right = Motor_CompensateMeasured(common, encoder_right, now, right_thresholds, right_state, limit) - yaw;
    }
    *left = Motor_ClampPwm(*left, limit);
    *right = Motor_ClampPwm(*right, limit);
}

static inline void Motor_MixCommands(int common, int yaw, int commanded_turn,
                                     int deadzone, int limit,
                                     int *left, int *right)
{
    if (commanded_turn)
    {
        /* Deliberate rotation still needs enough drive to start each wheel. */
        *left = Motor_CompensateDeadzone(common + yaw, deadzone);
        *right = Motor_CompensateDeadzone(common - yaw, deadzone);
    }
    else
    {
        /* Apply the common balance drive first. A tiny yaw correction near
         * common=0 must not become two opposing deadzone-sized impulses. */
        int drive = Motor_CompensateDeadzone(common, deadzone);
        *left = drive + yaw;
        *right = drive - yaw;
    }
    *left = Motor_ClampPwm(*left, limit);
    *right = Motor_ClampPwm(*right, limit);
}

#endif
