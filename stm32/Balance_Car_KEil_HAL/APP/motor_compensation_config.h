#ifndef MOTOR_COMPENSATION_CONFIG_H
#define MOTOR_COMPENSATION_CONFIG_H

/* Manual unloaded remeasurement, 2026-10-04, battery 12.193 V.
 * These are raw PWM thresholds, not PID gains or a loaded motor model. */
#define MOTOR_LEFT_FORWARD_START_PWM 1480
#define MOTOR_LEFT_FORWARD_RUN_PWM   1380
#define MOTOR_LEFT_REVERSE_START_PWM 1480
#define MOTOR_LEFT_REVERSE_RUN_PWM   1380
#define MOTOR_RIGHT_FORWARD_START_PWM 1490
#define MOTOR_RIGHT_FORWARD_RUN_PWM   1420
#define MOTOR_RIGHT_REVERSE_START_PWM 1490
#define MOTOR_RIGHT_REVERSE_RUN_PWM   1420

/* Controller settings for physical verification, not motor measurements.
 * Widen the friction-offset blend from +/-40 to +/-60 controller PWM for
 * the 2026-10-04 free-standing sway comparison. Keep this controller setting
 * while comparing the remeasured thresholds against the previous set.
 * 60 remains below both start-minus-run gaps (left 100, right 70), so either
 * stationary wheel can still receive the existing bounded start assist.
 * Ramp additions at 40 PWM/ms (200 per nominal 5 ms); PD/PI drive is direct.
 * Start help requires 50 ms without encoder pulses and 15 ms stable demand,
 * lasts <=60 ms, and cannot repeat sooner than 250 ms. A failed attempt stays
 * latched until movement is observed or zero demand persists for 100 ms. */
#define MOTOR_COMP_BLEND_PWM 60
#define MOTOR_COMP_RISE_PWM_PER_MS 40
#define MOTOR_COMP_QUIET_MS 50U
#define MOTOR_COMP_DEMAND_MS 15U
#define MOTOR_COMP_ASSIST_MS 60U
#define MOTOR_COMP_NEUTRAL_MS 100U
#define MOTOR_COMP_COOLDOWN_MS 250U

#endif
