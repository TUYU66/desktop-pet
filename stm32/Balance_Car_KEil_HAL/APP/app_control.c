#include "app_control.h"
#include "calibration_deadline.h"
#include "imu_sample.h"
#include "gyro_calibration.h"


volatile float battery = 12.0f;
volatile u8 angle_sample_valid = 0;
volatile uint32_t angle_last_update_ms = 0;
volatile u8 chassis_rest_state = 0;
volatile u8 chassis_rest_event = 0;
static volatile uint32_t rest_started_ms;
static volatile uint32_t rest_last_update_ms;
static volatile float rest_start_angle;
static volatile float rest_target_offset;
static volatile float rest_offset_limit;
static volatile u8 rest_rear_samples;
static volatile uint32_t balance_started_ms;
static volatile u8 balance_watchdog_armed;
volatile u8 chassis_turn_event = 0;
volatile u8 chassis_turn_error = 0;
/* 0=idle, 1=rising, 2=stabilizing before turn, 3=turning,
 * 4=stabilizing after turn, 5=returning to rear support. */
static volatile u8 turn_state;
static volatile u8 turn_return_to_rest;
static volatile u8 turn_direction;
static volatile u8 turn_stable_samples;
static volatile u8 turn_tilt_samples;
static volatile u8 rear_support_samples;
static volatile unsigned int standing_samples;
static volatile float turn_degrees;
static volatile long turn_forward_counts;
static volatile uint32_t turn_started_ms;
static volatile uint32_t turn_last_imu_ms;
static volatile float turn_drive;
static volatile float turn_progress_mark;
static volatile uint32_t turn_progress_ms;
static volatile int diagnostic_left, diagnostic_right;
static volatile uint32_t diagnostic_imu_dt;
static volatile uint32_t diagnostic_imu_max_dt;
static volatile u8 diagnostic_error, diagnostic_error_phase;
static volatile u8 bluetooth_control_enabled;
static volatile uint32_t bluetooth_command_ms;

typedef struct {
    u8 enabled;
    uint32_t samples, left_reversals, right_reversals;
    int balance, velocity, yaw, common_min, common_max;
    long left_counts, right_counts;
} ControlDiagnostics;
static volatile ControlDiagnostics control_diag;
static int control_left_sign, control_right_sign, control_common_sign;
typedef struct {
    uint32_t samples, command_free_samples, left_limit, right_limit, common_reversals;
    uint32_t min_dt, max_dt;
    long left_travel, right_travel, yaw_raw_sum;
    float min_angle, max_angle;
} ObservationDiagnostics;
static volatile ObservationDiagnostics observation_diag;

/* Only explicit operator commands enter this mode. No inferred calibration. */
static volatile GyroCalibration gyro_cal;
static volatile float gyro_pitch_raw, gyro_yaw_raw;

static volatile u8 calibration_active, calibration_lockout, calibration_wheel;
static volatile u8 calibration_reason, calibration_release_required, calibration_key_blocked;
static volatile unsigned int calibration_session;
static volatile int calibration_pwm;
static volatile uint32_t calibration_command_ms, calibration_run_ms;
static volatile uint32_t calibration_key_release_ms;
static volatile long calibration_left, calibration_right, calibration_abs_left, calibration_abs_right;

u8 Chassis_TuningWritable(void)
{
    /* Call with interrupts masked. Never alter gains during active balancing. */
    return Stop_Flag && !calibration_active && !Chassis_GyroActive() && !turn_state &&
           !bluetooth_control_enabled && (chassis_rest_state == 0 || chassis_rest_state == 2) &&
           angle_sample_valid && (uint32_t)(HAL_GetTick() - angle_last_update_ms) < 150U &&
           !lower_power_flag && Battery_IsReady(0);
}

unsigned int Chassis_FormatLive(char *text, unsigned int size, unsigned int revision)
{
    static long v[19];
    unsigned int captured_revision;
    float filtered, integral;
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    v[0] = (long)(angle_last_update_ms & 0x7FFFFFFFUL);
    v[1] = revision;
    v[2] = control_diag.enabled && !Stop_Flag && angle_sample_valid &&
           (uint32_t)(HAL_GetTick() - angle_last_update_ms) < 150U;
    v[3] = (long)(Angle_Balance * 100.0f);
    v[4] = (long)gyro_pitch_raw; v[5] = (long)gyro_yaw_raw;
    v[6] = diagnostic_left; v[7] = diagnostic_right;
    v[8] = control_diag.balance; v[9] = control_diag.velocity; v[10] = control_diag.yaw;
    v[11] = calibration_active && calibration_wheel == 1 ? calibration_pwm : v[2] ? Motor_Left : 0;
    v[12] = calibration_active && calibration_wheel == 2 ? calibration_pwm : v[2] ? Motor_Right : 0;
    PID_GetVelocityState(&filtered, &integral);
    v[13] = (long)integral; v[14] = (long)(filtered * 100.0f);
    v[15] = v[2] ? Motor_GetAssistMask() : 0;
    v[16] = (turn_state ? 1 : 0) | (chassis_rest_state == 1 ? 2 : 0) |
            (bluetooth_control_enabled ? 4 : 0) |
            ((Move_X != 0 || Move_Z != 0 || g_newcarstate != enSTOP) ? 8 : 0) |
            (calibration_active ? 16 : 0) | ((!Stop_Flag && standing_samples < 100) ? 32 : 0);
    captured_revision = gyro_cal.revision;
    v[17] = (long)(Gyro_Balance * 100.0f); v[18] = (long)(Gyro_Turn * 100.0f);
    __set_PRIMASK(irq);
    /* A 10 Hz snapshot, not all 200 Hz control samples. No UART in the ISR. */
    snprintf(text, size, "DIAG,LIVE,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld\n",
             v[0],v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8],v[9],v[10],v[11],v[12],v[13],v[14],v[15],v[16],v[17],v[18]);
    return captured_revision;
}

u8 Chassis_GyroActive(void) { return gyro_cal.state == 1; }
unsigned int Chassis_GyroRevision(void) { return gyro_cal.revision; }

u8 Chassis_GyroCommand(u8 operation)
{
    uint32_t irq = __get_PRIMASK();
    u8 error = 0;
    __disable_irq();
    if (operation == 2) {
        if (!Chassis_GyroActive()) error = 6;
        else GyroCal_Fail(&gyro_cal, 6);
    }
    else if (!Chassis_TuningWritable() || L_PWMA || L_PWMB || R_PWMA || R_PWMB ||
             Move_X || Move_Z || g_newcarstate != enSTOP ||
             !HAL_GPIO_ReadPin(KEY1_GPIO_Port, KEY1_Pin) || GET_Angle_Way != 2) error = 2;
    else if (operation == 1) {
        if (diagnostic_left || diagnostic_right) error = 2;
        else {
            calibration_lockout = 1; /* Only explicit Start may resume after this window. */
            GyroCal_Begin(&gyro_cal, HAL_GetTick(), Angle_Balance, gyro_pitch_raw, gyro_yaw_raw);
        }
    } else if (operation == 3) GyroCal_Clear(&gyro_cal);
    else error = 3;
    /* Remain stopped; no calibration command resumes balance. */
    if (!error) {
        Gyro_Balance = gyro_pitch_raw - (gyro_cal.valid ? gyro_cal.pitch_bias : 0);
        Gyro_Turn = gyro_yaw_raw - (gyro_cal.valid ? gyro_cal.yaw_bias : 0);
    }
    __set_PRIMASK(irq);
    return error;
}

void Chassis_GyroWatchdog(void)
{
    uint32_t irq = __get_PRIMASK(), now;
    __disable_irq();
    /* Snapshot both clocks with IRQs masked; a newer IMU tick is not stale. */
    now = HAL_GetTick();
    if (Chassis_GyroActive()) {
        /* A failed read contributes no sample; only a sustained gap aborts. */
        if ((uint32_t)(now - angle_last_update_ms) > GYRO_CAL_MAX_GAP_MS) GyroCal_Fail(&gyro_cal, 3);
        else if ((uint32_t)(now - gyro_cal.started) > 5000U) GyroCal_Fail(&gyro_cal, 4);
    }
    __set_PRIMASK(irq);
}

unsigned int Chassis_FormatGyro(char *text, unsigned int size, unsigned int revision)
{
    static long fields[13];
    uint32_t irq = __get_PRIMASK();
    unsigned int progress;
    __disable_irq();
    progress = gyro_cal.state == 1 ? (unsigned int)((uint32_t)(HAL_GetTick()-gyro_cal.started)/30U) : gyro_cal.state == 2 ? 100 : 0;
    if (progress > 99 && gyro_cal.state == 1) progress = 99;
    fields[0] = (long)(angle_last_update_ms & 0x7FFFFFFFUL);
    fields[1] = gyro_cal.revision; fields[2] = gyro_cal.state; fields[3] = gyro_cal.error;
    fields[4] = progress; fields[5] = gyro_cal.valid;
    fields[6] = (long)(gyro_cal.pitch_bias * 100.0f); fields[7] = (long)(gyro_cal.yaw_bias * 100.0f);
    fields[8] = (long)gyro_pitch_raw; fields[9] = (long)gyro_yaw_raw;
    fields[10] = (long)(Gyro_Balance * 100.0f); fields[11] = (long)(Gyro_Turn * 100.0f); fields[12] = revision;
    __set_PRIMASK(irq);
    snprintf(text, size, "GSTATE,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld\n",
             fields[0],fields[1],fields[2],fields[3],fields[4],fields[5],fields[6],fields[7],fields[8],fields[9],fields[10],fields[11],fields[12]);
    return (unsigned int)fields[1];
}

u8 Chassis_CalibrationActive(void) { return calibration_active; }
u8 Chassis_CalibrationKeyBlocked(void) { return calibration_active || calibration_key_blocked || Chassis_GyroActive(); }

/* Caller masks interrupts. Every stop removes physical output immediately. */
static void calibration_stop(u8 reason, u8 exit_mode)
{
    calibration_pwm = 0;
    Motor_ResetBalanceCompensation();
    Motor_Left = Motor_Right = 0;
    Set_Pwm(0, 0);
    Stop_Flag = 1;
    calibration_lockout = 1;
    calibration_reason = reason;
    if (reason == 6) { calibration_key_blocked = 1; calibration_key_release_ms = 0; }
    if (exit_mode) {
        calibration_active = 0;
        Bluetooth_DiscardInput(); /* Discard phone packets received during bench mode. */
    }
}

void Chassis_CalibrationWatchdog(void)
{
    uint32_t irq, now;
    /* Require 100 ms continuously released. A contact bounce must not turn the
     * emergency-stop press into the boot loop's normal start press. */
    if (calibration_key_blocked) {
        if (HAL_GPIO_ReadPin(KEY1_GPIO_Port, KEY1_Pin)) {
            if (!calibration_key_release_ms) calibration_key_release_ms = HAL_GetTick();
            else if ((uint32_t)(HAL_GetTick() - calibration_key_release_ms) >= 100U) calibration_key_blocked = 0;
        } else calibration_key_release_ms = 0;
    }
    if (!calibration_active) return;
    irq = __get_PRIMASK();
    __disable_irq();
    now = HAL_GetTick();
    if (lower_power_flag || battery < 9.6f || !Battery_IsReady(0)) calibration_stop(3, 1);
    else if (!angle_sample_valid || (uint32_t)(now - angle_last_update_ms) > 150U) calibration_stop(4, 1);
    else if (Angle_Balance < -40.0f || Angle_Balance > angle_max) calibration_stop(5, 1);
    else if (!HAL_GPIO_ReadPin(KEY1_GPIO_Port, KEY1_Pin)) calibration_stop(6, 1);
    else if ((uint32_t)(now - calibration_command_ms) >= CALIBRATION_IDLE_MS) calibration_stop(2, 1);
    else if (calibration_pwm && (uint32_t)(now - calibration_run_ms) >= CALIBRATION_RUN_MS) {
        calibration_release_required = 1;
        calibration_stop(7, 0);
    } else if (calibration_pwm && (uint32_t)(now - calibration_command_ms) >= CALIBRATION_LEASE_MS) {
        calibration_release_required = 1;
        calibration_stop(1, 0);
    }
    __set_PRIMASK(irq);
}

u8 Chassis_CalibrationCommand(u8 operation, unsigned int session, u8 wheel, int pwm, uint32_t sample_tick)
{
    uint32_t irq = __get_PRIMASK();
    u8 result = 0;
    __disable_irq();
    if (operation == 3) { /* EXIT is allowed even after a lease/session fault. */
        if (calibration_active || calibration_lockout) calibration_stop(8, 1);
    } else if (operation == 1) {
        if (calibration_active || Chassis_GyroActive() || turn_state || chassis_rest_state == 1 || bluetooth_control_enabled) result = 5;
        else if (!Stop_Flag) result = 4;
        else if (chassis_rest_state == 3) result = 5;
        else if (!angle_sample_valid || (uint32_t)(HAL_GetTick() - angle_last_update_ms) > 150U) result = 1;
        else if (!Battery_IsReady(0) || lower_power_flag || battery < 9.6f) result = 2;
        else if (Angle_Balance < -40.0f || Angle_Balance > angle_max) result = 3;
        else if (!session || calibration_key_blocked || !HAL_GPIO_ReadPin(KEY1_GPIO_Port, KEY1_Pin)) result = 6;
        else {
            calibration_session = session;
            calibration_active = calibration_lockout = 1;
            calibration_wheel = calibration_reason = calibration_release_required = 0;
            calibration_pwm = 0;
            calibration_left = calibration_right = calibration_abs_left = calibration_abs_right = 0;
            calibration_command_ms = HAL_GetTick();
            g_newcarstate = enSTOP;
            Move_X = Move_Z = 0;
            Motor_Left = Motor_Right = 0;
            Set_Pwm(0, 0);
            standing_samples = rear_support_samples = 0;
            balance_watchdog_armed = 0;
            Bluetooth_DiscardInput();
            PID_ResetVelocity();
            Motor_ResetBalanceCompensation();
        }
    } else if (operation == 2) {
        Chassis_CalibrationWatchdog();
        if (!calibration_active || session != calibration_session) result = 6;
        else if (wheel < 1 || wheel > 2 || pwm < -MOTOR_BALANCE_PWM_LIMIT || pwm > MOTOR_BALANCE_PWM_LIMIT) result = 7;
        else if (pwm && !Calibration_SampleFresh(HAL_GetTick(), sample_tick)) result = 6;
        else if (pwm && calibration_release_required) result = 8;
        else if (calibration_pwm && pwm && (wheel != calibration_wheel || (pwm > 0) != (calibration_pwm > 0))) result = 5;
        else {
            if (pwm != calibration_pwm || wheel != calibration_wheel) {
                calibration_left = calibration_right = calibration_abs_left = calibration_abs_right = 0;
            }
            if (!calibration_pwm && pwm) calibration_run_ms = HAL_GetTick();
            /* A delayed packet cannot acquire a new full lease. Its deadline
             * remains tied to the operator's observed sample clock. */
            calibration_command_ms = HAL_GetTick();
            if (pwm) calibration_command_ms = Calibration_LeaseStart(calibration_command_ms, sample_tick);
            calibration_wheel = wheel;
            calibration_pwm = pwm;
            calibration_reason = 0;
            if (!pwm) calibration_release_required = 0;
            Motor_Left = wheel == 1 ? pwm : 0;
            Motor_Right = wheel == 2 ? pwm : 0;
            Set_Pwm(Motor_Left, Motor_Right); /* raw, no 1300 compensation */
        }
    } else result = 7;
    __set_PRIMASK(irq);
    return result;
}

static u8 calibration_on_imu(int left, int right)
{
    uint32_t irq = __get_PRIMASK();
    u8 intercept;
    __disable_irq();
    Chassis_CalibrationWatchdog();
    intercept = calibration_active || calibration_lockout;
    if (intercept) {
        Stop_Flag = 1;
        if (calibration_active && calibration_pwm) {
            calibration_left += left; calibration_right += right;
            calibration_abs_left += myabs(left); calibration_abs_right += myabs(right);
            Motor_Left = calibration_wheel == 1 ? calibration_pwm : 0;
            Motor_Right = calibration_wheel == 2 ? calibration_pwm : 0;
            Set_Pwm(Motor_Left, Motor_Right);
        } else { Motor_Left = Motor_Right = 0; Set_Pwm(0, 0); }
    }
    __set_PRIMASK(irq);
    return intercept;
}

void Chassis_FormatCalibration(char *text, unsigned int size)
{
    uint32_t irq = __get_PRIMASK(), tick, run;
    u8 active, wheel, reason;
    unsigned int session;
    int pwm;
    long left, right, abs_left, abs_right, mv;
    __disable_irq();
    tick = HAL_GetTick(); active = calibration_active; session = calibration_session;
    wheel = calibration_wheel; pwm = calibration_pwm; reason = calibration_reason;
    left = calibration_left; right = calibration_right;
    abs_left = calibration_abs_left; abs_right = calibration_abs_right;
    run = pwm ? (uint32_t)(tick - calibration_run_ms) : 0;
    mv = (long)(battery * 1000.0f);
    __set_PRIMASK(irq);
    snprintf(text, size, "DIAG,CAL,%lu,%u,%u,%u,%d,%ld,%ld,%ld,%ld,%u,%lu,%ld\n",
             (unsigned long)tick, active, session, wheel, pwm, left, right,
             abs_left, abs_right, reason, (unsigned long)run, mv);
}

/* Called only by the IMU ISR. Keep UART and formatting in the foreground. */
static void record_control(u8 enabled, int balance, int velocity, int yaw,
                           int left_encoder, int right_encoder)
{
    int common = balance + velocity;
    int left_sign = (Motor_Left > 0) - (Motor_Left < 0);
    int right_sign = (Motor_Right > 0) - (Motor_Right < 0);
    int common_sign = (common > 0) - (common < 0);
    int limit = chassis_rest_state == 1 ? MOTOR_REST_PWM_LIMIT : MOTOR_BALANCE_PWM_LIMIT;
    if (!observation_diag.samples || Angle_Balance < observation_diag.min_angle)
        observation_diag.min_angle = Angle_Balance;
    if (!observation_diag.samples || Angle_Balance > observation_diag.max_angle)
        observation_diag.max_angle = Angle_Balance;
    observation_diag.samples++;
    observation_diag.yaw_raw_sum += (long)Gyro_Turn;
    if (diagnostic_imu_dt) {
        if (!observation_diag.min_dt || diagnostic_imu_dt < observation_diag.min_dt)
            observation_diag.min_dt = diagnostic_imu_dt;
        if (diagnostic_imu_dt > observation_diag.max_dt)
            observation_diag.max_dt = diagnostic_imu_dt;
    }
    control_diag.enabled = enabled;
    control_diag.balance = balance;
    control_diag.velocity = velocity;
    control_diag.yaw = yaw;
    if (!enabled) {
        control_left_sign = control_right_sign = control_common_sign = 0;
        return;
    }
    if (!control_diag.samples || common < control_diag.common_min) control_diag.common_min = common;
    if (!control_diag.samples || common > control_diag.common_max) control_diag.common_max = common;
    control_diag.samples++;
    /* Command-free is a label, not a new control mode or proof of settling. */
    if (!turn_state && !chassis_rest_state && g_newcarstate == enSTOP && Move_X == 0 && Move_Z == 0)
        observation_diag.command_free_samples++;
    observation_diag.left_travel += myabs(left_encoder);
    observation_diag.right_travel += myabs(right_encoder);
    if (myabs(Motor_Left) >= limit) observation_diag.left_limit++;
    if (myabs(Motor_Right) >= limit) observation_diag.right_limit++;
    if (common_sign) {
        if (control_common_sign && common_sign != control_common_sign) observation_diag.common_reversals++;
        control_common_sign = common_sign;
    }
    control_diag.left_counts += left_encoder;
    control_diag.right_counts += right_encoder;
    if (left_sign) {
        if (control_left_sign && left_sign != control_left_sign) control_diag.left_reversals++;
        control_left_sign = left_sign;
    }
    if (right_sign) {
        if (control_right_sign && right_sign != control_right_sign) control_diag.right_reversals++;
        control_right_sign = right_sign;
    }
}

u8 Chassis_BluetoothEnabled(void) { return bluetooth_control_enabled; }

u8 Chassis_IsSeated(void)
{
    u8 seated;
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    seated = Stop_Flag && (chassis_rest_state == 0 || chassis_rest_state == 2) &&
             rear_support_samples >= 40 && angle_sample_valid &&
             (uint32_t)(HAL_GetTick() - angle_last_update_ms) <= 150U;
    __set_PRIMASK(irq);
    return seated;
}

u8 Chassis_SetBluetooth(u8 enabled)
{
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    if (enabled && (calibration_active || Chassis_GyroActive() || turn_state || chassis_rest_state != 0 || Stop_Flag ||
        standing_samples < 100 || !angle_sample_valid ||
        (uint32_t)(HAL_GetTick() - angle_last_update_ms) > 150U || lower_power_flag || battery < 9.6f)) {
        __set_PRIMASK(irq);
        return 6;
    }
    /* Disabling an inactive mode must not cancel an unrelated voice action. */
    if (enabled || bluetooth_control_enabled) {
        g_newcarstate = enSTOP;
        Move_X = 0;
        Move_Z = 0;
        standing_samples = 0;
    }
    bluetooth_control_enabled = enabled ? 1 : 0;
    bluetooth_command_ms = HAL_GetTick();
    __set_PRIMASK(irq);
    return 0;
}

void Chassis_BluetoothMove(enCarState state)
{
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    if (bluetooth_control_enabled && !turn_state && chassis_rest_state != 1) {
        bluetooth_command_ms = HAL_GetTick();
        g_newcarstate = Stop_Flag || lower_power_flag ? enSTOP : state;
        Move_X = 0;
        Move_Z = 0;
    }
    __set_PRIMASK(irq);
}

void Chassis_BluetoothWatchdog(void)
{
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    if (bluetooth_control_enabled && (Stop_Flag || lower_power_flag ||
        (uint32_t)(HAL_GetTick() - bluetooth_command_ms) >= 2000U)) {
        g_newcarstate = enSTOP;
        Move_X = 0;
        Move_Z = 0;
    }
    __set_PRIMASK(irq);
}
typedef struct {
    uint32_t failed[6]; /* stopped, angle, pitch gyro, yaw gyro, encoder, drift */
    uint32_t resets, center_wait, rotate_dt, rotate_max_dt, skipped_count, skipped_ms;
    u8 last_mask, previous_samples, peak_samples;
} TurnDiagnostics;
static volatile TurnDiagnostics turn_diag;

static void record_stable_failure(u8 mask)
{
    unsigned int i;
    for (i = 0; i < 6; ++i) if (mask & (1U << i)) turn_diag.failed[i]++;
    if (turn_stable_samples) {
        turn_diag.resets++;
        turn_diag.last_mask = mask;
        turn_diag.previous_samples = turn_stable_samples;
    }
}

#define TURN_RISING 1
#define TURN_BEFORE 2
#define TURN_ROTATING 3
#define TURN_AFTER 4
#define TURN_RESTING 5
#define TURN_STABLE_SAMPLES 100
#define TURN_STABLE_DRIFT_COUNTS 350L
/* With the MPU6050 mounted face up, a right yaw reads negative on Z. */
#define TURN_RIGHT_GYRO_SIGN (-1.0f)

u8 Chassis_TurnBusy(void)
{
    return turn_state != 0;
}

/* Foreground only; diagnostics are retained until the next accepted turn. */
void Chassis_FormatTurnDiagnostics(unsigned int sequence, char *stable, unsigned int stable_size,
                                   char *timing, unsigned int timing_size)
{
    static TurnDiagnostics snapshot;
    u8 phase, samples;
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    snapshot = turn_diag;
    phase = turn_state;
    samples = turn_stable_samples;
    __set_PRIMASK(irq);
    snprintf(stable, stable_size,
             "DIAG,STABLE,%u,%u,%u,%u,%u,%u,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu\n",
             sequence, phase, samples, snapshot.peak_samples, snapshot.last_mask,
             snapshot.previous_samples, (unsigned long)snapshot.resets,
             (unsigned long)snapshot.failed[0], (unsigned long)snapshot.failed[1],
             (unsigned long)snapshot.failed[2], (unsigned long)snapshot.failed[3],
             (unsigned long)snapshot.failed[4], (unsigned long)snapshot.failed[5],
             (unsigned long)snapshot.center_wait);
    snprintf(timing, timing_size, "DIAG,TURN_DT,%u,%lu,%lu,%lu,%lu\n", sequence,
             (unsigned long)snapshot.rotate_dt, (unsigned long)snapshot.rotate_max_dt,
             (unsigned long)snapshot.skipped_count, (unsigned long)snapshot.skipped_ms);
}

static u8 turn_stance_failure(int encoder_left, int encoder_right)
{
    /* Fore/aft corrections are normal for a balancing car. Check that each
     * sample stays controlled; the signed encoder sum below checks drift. */
    u8 mask = 0;
    if (Stop_Flag != 0) mask |= 1;
    if (!(Angle_Balance >= -10.0f && Angle_Balance <= 10.0f)) mask |= 2;
    if (!(Gyro_Balance > -500.0f && Gyro_Balance < 500.0f)) mask |= 4;
    if (!(Gyro_Turn > -164.0f && Gyro_Turn < 164.0f)) mask |= 8;
    if (!(myabs(encoder_left) + myabs(encoder_right) < 120)) mask |= 16;
    return mask;
}

/* 1=tilt, 2=balance stopped, 3=low battery, 4=stale IMU,
 * 5=turn timeout, 6=rest failed, 7=rest timeout, 8=stand timeout,
 * 9=stance did not settle, 10=yaw moved the wrong way. */
static void turn_abort(u8 reason)
{
    diagnostic_error = reason;
    diagnostic_error_phase = turn_state;
    g_newcarstate = enSTOP;
    Move_X = 0;
    Move_Z = 0;
    if (reason == 4 || (turn_state == TURN_RISING && reason != 1))
    {
        Stop_Flag = 1;
        if (reason == 4) chassis_rest_state = 3;
        Set_Pwm(0, 0);
    }
    if (turn_state == TURN_RESTING && chassis_rest_state == 1) Chassis_CancelRest();
    turn_state = 0;
    chassis_turn_error = reason;
    chassis_turn_event = 2;
}

u8 Chassis_BeginTurn(u8 direction)
{
    uint32_t angle_updated = angle_last_update_ms;
    uint32_t now = HAL_GetTick();
    u8 seated;
    if (Chassis_GyroActive() || bluetooth_control_enabled || direction < 1 || direction > 3 || turn_state != 0 || chassis_rest_state == 1)
        return 6;
    if (chassis_rest_state == 3) return 5;
    if (!angle_sample_valid || (uint32_t)(now - angle_updated) > 150U ||
        !Battery_IsReady(0)) return 1;
    if (lower_power_flag || battery < 9.6f) return 2;
    /* A motor stop alone can also mean a fault or pickup. Require a quiet,
     * sustained rear tilt before treating it as seated on the support. */
    seated = Stop_Flag == 1 &&
             (chassis_rest_state == 0 || chassis_rest_state == 2) &&
             rear_support_samples >= 40;
    if (Stop_Flag == 1 && !seated) return 4;
    /* The balancing body can cross +/-5 degrees during normal corrections.
     * The following phase waits for sustained stability before applying yaw. */
    if (!seated && (Angle_Balance < -18.0f || Angle_Balance > 18.0f)) return 3;

    turn_direction = direction;
    {
        TurnDiagnostics empty = {0};
        turn_diag = empty; /* Idle: IMU callback does not update these counters. */
    }
    diagnostic_error = 0;
    diagnostic_error_phase = 0;
    turn_return_to_rest = seated;
    turn_stable_samples = 0;
    turn_forward_counts = 0;
    turn_tilt_samples = 0;
    turn_degrees = 0;
    turn_started_ms = now;
    turn_last_imu_ms = now;
    chassis_turn_event = 0;
    chassis_turn_error = 0;
    g_newcarstate = enSTOP;
    Move_X = 0;
    Move_Z = 0;
    if (seated)
    {
        /* Reuse exactly the working standalone STAND motor-start path.
         * No yaw command or upright tilt guard runs during the rise. */
        u8 result = Chassis_StartBalance();
        if (result) return result;
        turn_state = TURN_RISING;
    }
    else turn_state = TURN_BEFORE;
    return 0;
}

static void turn_watchdog_locked(void)
{
    uint32_t angle_updated = angle_last_update_ms;
    uint32_t now = HAL_GetTick();
    if (turn_state == 0) return;
    if (turn_state == TURN_RESTING)
    {
        if ((uint32_t)(now - angle_updated) > 150U)
            turn_abort(4);
        else if (chassis_rest_state == 2 && rear_support_samples >= 40)
        {
            turn_state = 0;
            chassis_turn_event = 1;
        }
        else if (chassis_rest_state == 0 || chassis_rest_state == 3 ||
                 chassis_rest_event == 2 || chassis_rest_event == 3)
            turn_abort(6);
        else if ((uint32_t)(now - turn_started_ms) > 3000U)
            turn_abort(7);
        return;
    }
    if (Stop_Flag == 1)
        turn_abort(chassis_rest_state == 3 ? 4 : 2);
    else if (lower_power_flag)
        turn_abort(3);
    /* During the rise the normal balance watchdog owns the MPU timeout;
     * the other phases require a fresh sample at all times. */
    else if (turn_state != TURN_RISING &&
             (uint32_t)(now - angle_updated) > 150U)
        turn_abort(4);
    else if (turn_state == TURN_RISING &&
             (uint32_t)(now - turn_started_ms) > 6000U)
        turn_abort(8);
    else if ((turn_state == TURN_BEFORE || turn_state == TURN_AFTER) &&
             (uint32_t)(now - turn_started_ms) > 6000U)
        turn_abort(9);
    else if (turn_state == TURN_ROTATING &&
             (uint32_t)(now - turn_started_ms) >
             (turn_direction == 3 ? 24000U : 14000U))
        turn_abort(5);
}

void Chassis_TurnWatchdog(void)
{
    /* IMU ISR changes both phase and its start tick. Keep the check and
     * resulting transition atomic: old now - new start can wrap to ~49 days.
     * No formatting, serial IO or waiting is allowed in this short section. */
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    turn_watchdog_locked();
    __set_PRIMASK(irq);
}

u8 Chassis_TurnFailurePhase(void)
{
    return diagnostic_error_phase;
}

static void turn_on_imu(int encoder_left, int encoder_right)
{
    uint32_t now = HAL_GetTick();
    uint32_t dt = now - turn_last_imu_ms;
    float rate;
    float command;
    turn_last_imu_ms = now;
    if (turn_state == 0 || turn_state == TURN_RESTING) return;
    if (turn_state == TURN_RISING)
    {
        /* Allow the rear support angle here, just as standalone STAND does. */
        if (Angle_Balance >= -8.0f && Angle_Balance <= 8.0f)
        {
            turn_state = TURN_BEFORE;
            turn_started_ms = now;
            turn_stable_samples = 0;
            turn_forward_counts = 0;
        }
        return;
    }
    if (Angle_Balance < -30.0f || Angle_Balance > 30.0f)
    {
        turn_abort(1);
        return;
    }
    if (Angle_Balance < -18.0f || Angle_Balance > 18.0f)
    {
        if (++turn_tilt_samples >= 12)
        {
            turn_abort(1);
            return;
        }
    }
    else turn_tilt_samples = 0;
    if (turn_state == TURN_BEFORE || turn_state == TURN_AFTER)
    {
        u8 failure = turn_stance_failure(encoder_left, encoder_right);
        if (!failure)
        {
            turn_forward_counts += encoder_left + encoder_right;
            if (turn_forward_counts < -TURN_STABLE_DRIFT_COUNTS ||
                turn_forward_counts > TURN_STABLE_DRIFT_COUNTS)
            {
                record_stable_failure(32);
                turn_stable_samples = 0;
                turn_forward_counts = 0;
                return;
            }
            if (turn_stable_samples < TURN_STABLE_SAMPLES)
                turn_stable_samples++;
            if (turn_stable_samples > turn_diag.peak_samples)
                turn_diag.peak_samples = turn_stable_samples;
            if (turn_stable_samples >= TURN_STABLE_SAMPLES &&
                !(Angle_Balance >= -7.0f && Angle_Balance <= 7.0f))
                turn_diag.center_wait++;
            /* Start yaw or rear lean near the center of the normal balancing
             * swing, after the whole stable window has been observed. */
            if (turn_stable_samples >= TURN_STABLE_SAMPLES &&
                Angle_Balance >= -7.0f && Angle_Balance <= 7.0f)
            {
                if (turn_state == TURN_BEFORE)
                {
                    turn_state = TURN_ROTATING;
                    turn_started_ms = now;
                    turn_last_imu_ms = now;
                    turn_degrees = 0;
                    turn_drive = 0;
                    turn_progress_mark = 0;
                    turn_progress_ms = now;
                }
                else if (turn_return_to_rest)
                {
                    if (Chassis_BeginRest() == 0)
                    {
                        turn_state = TURN_RESTING;
                        turn_started_ms = now;
                    }
                    else turn_abort(6);
                }
                else
                {
                    turn_state = 0;
                    chassis_turn_event = 1;
                }
            }
        }
        else
        {
            record_stable_failure(failure);
            turn_stable_samples = 0;
            turn_forward_counts = 0;
        }
        return;
    }
    if (turn_state == TURN_ROTATING)
    {
        turn_diag.rotate_dt = dt;
        if (dt > turn_diag.rotate_max_dt) turn_diag.rotate_max_dt = dt;
        if (dt > 20U) {
            turn_diag.skipped_count++;
            turn_diag.skipped_ms += dt;
        }
        /* Integrate signed yaw, so a reverse wobble subtracts from progress. */
        rate = TURN_RIGHT_GYRO_SIGN * Gyro_Turn / 16.4f;
        if (turn_direction == 1) rate = -rate;
        /* Retain slow real rotation; the former 5 dps cutoff lost progress. */
        if ((rate > 0.5f || rate < -0.5f) && dt <= 20U)
            turn_degrees += rate * dt / 1000.0f;
        if (turn_degrees <= -15.0f)
        {
            turn_abort(10);
            return;
        }
        if (turn_degrees >= (turn_direction == 3 ? 180.0f : 90.0f))
        {
            g_newcarstate = enSTOP;
            Move_Z = 0;
            turn_state = TURN_AFTER;
            turn_started_ms = now;
            turn_stable_samples = 0;
            turn_forward_counts = 0;
        }
        else
        {
            float remaining = (turn_direction == 3 ? 180.0f : 90.0f) - turn_degrees;
            float target_rate = remaining * 2.5f;
            float step = 240.0f * (dt > 20U ? 20U : dt) / 1000.0f;
            /* Faster cruise; keep the existing approach-speed curve, output
             * ceiling and slew limit. Previous setting: 40 dps. */
            if (target_rate > 100.0f) target_rate = 100.0f;
            if (target_rate < 8.0f) target_rate = 8.0f;
            /* Give balance corrections priority; yaw feedback compensates for
             * unequal left/right load without changing the balance PID. */
            if (Angle_Balance < -10.0f || Angle_Balance > 10.0f)
                target_rate *= 0.5f;
            command = 180.0f + 4.0f * (target_rate - rate);
            if (command < 0) command = 0;
            if (command > 360.0f) command = 360.0f;
            if (command > turn_drive + step) command = turn_drive + step;
            if (command < turn_drive - step) command = turn_drive - step;
            turn_drive = command;
            if (turn_degrees >= turn_progress_mark + 3.0f)
            {
                turn_progress_mark = turn_degrees;
                turn_progress_ms = now;
            }
            else if ((uint32_t)(now - turn_progress_ms) > 3000U)
            {
                turn_abort(11);
                return;
            }
            g_newcarstate = enSTOP;
            Move_Z = (turn_direction == 1 ? -1.0f : 1.0f) * command;
        }
    }
}

u8 Chassis_StartBalance(void)
{
    uint32_t irq = __get_PRIMASK(), now;
    u8 result = 0, seated;
    __disable_irq();
    now = HAL_GetTick();
    if (Chassis_GyroActive() || calibration_active || turn_state ||
        bluetooth_control_enabled) result = 6;
    else if (!angle_sample_valid || (uint32_t)(now - angle_last_update_ms) > 150U ||
             !Battery_IsReady(0)) result = 1;
    else if (lower_power_flag || battery < 9.6f) result = 2;
    else if (!(Angle_Balance >= -39.0f && Angle_Balance <= 18.0f)) result = 3;
    seated = Stop_Flag && (chassis_rest_state == 0 || chassis_rest_state == 2) &&
             rear_support_samples >= 40;
    if (!result && Stop_Flag && !seated &&
        !(Angle_Balance >= Mid_Angle - 10.0f && Angle_Balance <= Mid_Angle + 10.0f)) result = 4;
    if (result || (!Stop_Flag && chassis_rest_state == 0)) {
        /* An already standing STAND must not erase its displacement feedback. */
        __set_PRIMASK(irq);
        return result;
    }
    calibration_lockout = 0;
    g_newcarstate = enSTOP;
    Move_X = 0;
    Move_Z = 0;
    Chassis_CancelRest();
    chassis_rest_event = 0;
    rear_support_samples = 0;
    standing_samples = 0;
    balance_started_ms = now;
    balance_watchdog_armed = 1;
    PID_ResetVelocity();
    Motor_ResetBalanceCompensation();
    Stop_Flag = 0;
    __set_PRIMASK(irq);
    return 0;
}

u8 Chassis_PostureResult(u8 standing)
{
    u8 result = 0;
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    if (lower_power_flag || chassis_rest_state == 3 || !angle_sample_valid ||
        (uint32_t)(HAL_GetTick() - angle_last_update_ms) > 150U) result = 2;
    else if (standing) {
        if (Stop_Flag) result = 2;
        else if (standing_samples >= 100) result = 1;
    } else if (chassis_rest_state == 2 && Stop_Flag && rear_support_samples >= 40) result = 1;
    else if (chassis_rest_state == 0) result = 2;
    __set_PRIMASK(irq);
    return result;
}

u8 Chassis_MotionWatchdog(void)
{
    uint32_t irq = __get_PRIMASK(), now;
    u8 result = 0;
    __disable_irq();
    now = HAL_GetTick();
    if (balance_watchdog_armed && Stop_Flag == 0 &&
        (uint32_t)(now - balance_started_ms) > 500U &&
        (!angle_sample_valid || (uint32_t)(now - angle_last_update_ms) > 150U))
    {
        Stop_Flag = 1;
        chassis_rest_state = 3;
        Set_Pwm(0, 0);
        result = 1;
    }
    __set_PRIMASK(irq);
    return result;
}

/* Rear tilt is negative in the installed MPU orientation. Give the body only
 * a small rearward tendency, then release the motors to the rear support. */
u8 Chassis_BeginRest(void)
{
    if (Chassis_GyroActive()) return 6;
    uint32_t angle_updated = angle_last_update_ms;
    if (chassis_rest_state == 1 || chassis_rest_state == 2) return 0;
    if (chassis_rest_state == 3) return 5;
    if (Stop_Flag == 1) {
        /* Repeated REST / BT-off may find the car already on its support.
         * A stopped motor by itself is not sufficient evidence of sitting. */
        if (rear_support_samples >= 40) {
            chassis_rest_state = 2;
            return 0;
        }
        return 4;
    }
    if (!angle_sample_valid ||
        (uint32_t)(HAL_GetTick() - angle_updated) > 150U ||
        !Battery_IsReady(0)) return 1;
    if (lower_power_flag || battery < 9.6f) return 2;
    if (Angle_Balance < -7.0f || Angle_Balance > 7.0f) return 3;
    g_newcarstate = enSTOP;
    Move_X = 0;
    Move_Z = 0;
    rest_start_angle = Angle_Balance;
    rest_target_offset = 0;
    /* Rest uses an absolute rear tilt; a positive calibrated balance angle
     * must not cancel the lean and leave its target ahead of the -1.2 guard. */
    rest_offset_limit = -2.5f - (Mid_Angle > 0 ? Mid_Angle : 0);
    /* If already leaning rearward, still request a small further lean so the
     * relative completion guard cannot be left beyond the target angle. */
    if (Mid_Angle + rest_offset_limit > rest_start_angle - 1.0f)
        rest_offset_limit = rest_start_angle - 1.0f - Mid_Angle;
    rest_rear_samples = 0;
    chassis_rest_event = 0;
    rest_started_ms = HAL_GetTick();
    rest_last_update_ms = rest_started_ms;
    chassis_rest_state = 1;
    return 0;
}

void Chassis_CancelRest(void)
{
    if (chassis_rest_state == 1) chassis_rest_event = 2;
    chassis_rest_state = 0;
    rest_target_offset = 0;
    rest_rear_samples = 0;
}

void Chassis_RestWatchdog(void)
{
    uint32_t rest_updated = rest_last_update_ms;
    if (chassis_rest_state == 1 &&
        (uint32_t)(HAL_GetTick() - rest_updated) > 150U)
    {
        Stop_Flag = 1;
        chassis_rest_state = 3;
        chassis_rest_event = 3;
        Set_Pwm(0, 0);
    }
}

void Chassis_FormatDiagnostics(char *motion, unsigned int motion_size,
                               char *sensors, unsigned int sensors_size)
{
    uint32_t irq = __get_PRIMASK();
    uint32_t updated, dt, max_dt, now;
    u8 phase, error, error_phase, stopped, rest, stable, valid;
    int left, right, pwm_left, pwm_right;
    float angle, pitch_rate, yaw_rate, degrees;
    long drift;
    /* Only copy shared data here. Formatting and UART writes must remain in
     * the foreground with interrupts restored, never in the balance ISR. */
    __disable_irq();
    updated = angle_last_update_ms;
    valid = angle_sample_valid;
    dt = diagnostic_imu_dt;
    max_dt = diagnostic_imu_max_dt;
    diagnostic_imu_max_dt = 0;
    phase = turn_state; error = diagnostic_error;
    error_phase = diagnostic_error_phase;
    stopped = Stop_Flag; rest = chassis_rest_state;
    stable = turn_stable_samples; drift = turn_forward_counts;
    left = diagnostic_left; right = diagnostic_right;
    pwm_left = Motor_Left; pwm_right = Motor_Right;
    angle = Angle_Balance; pitch_rate = Gyro_Balance;
    yaw_rate = Gyro_Turn; degrees = turn_degrees;
    __set_PRIMASK(irq);
    now = HAL_GetTick();
    /* Fixed-point angles avoid float printf support in the embedded library. */
    snprintf(motion, motion_size,
             "DIAG,M,%lu,%u,%u,%u,%u,%u,%u,%ld,%ld\n",
             (unsigned long)now, phase, error, error_phase, stopped, rest,
             stable, drift, (long)(degrees * 10.0f));
    snprintf(sensors, sensors_size,
             "DIAG,S,%lu,%u,%lu,%lu,%lu,%ld,%ld,%ld,%d,%d,%d,%d\n",
             (unsigned long)now, valid, (unsigned long)(now - updated),
             (unsigned long)dt, (unsigned long)max_dt,
             (long)(angle * 10.0f), (long)pitch_rate, (long)yaw_rate,
             left, right, pwm_left, pwm_right);
}

void Chassis_FormatBaseline(char *text, unsigned int size)
{
    /* CFG retains the legacy rest deadzone. Normal balance thresholds are COMP. */
    snprintf(text, size, "DIAG,CFG,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%u,%u,%u\n",
             (long)(Mid_Angle * 1000.0f), (long)(Balance_Kp * 100.0f), (long)(Balance_Kd * 100.0f),
             (long)(Velocity_Kp * 100.0f), (long)(Velocity_Ki * 100.0f),
             (long)(Turn_Kp * 100.0f), (long)(Turn_Kd * 100.0f), (long)(myTurn_Kd * 100.0f),
             (unsigned int)MOTOR_IGNORE_PULSE, (unsigned int)MOTOR_BALANCE_PWM_LIMIT,
             (unsigned int)Control_Frequency);
}

void Chassis_FormatControlDiagnostics(char *text, unsigned int size,
                                      char *observations, unsigned int observations_size)
{
    static ControlDiagnostics snapshot;
    static ObservationDiagnostics observed;
    ObservationDiagnostics empty = {0};
    uint32_t tick;
    float filtered, integral;
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    tick = HAL_GetTick();
    snapshot = control_diag;
    observed = observation_diag;
    observation_diag = empty;
    PID_GetVelocityState(&filtered, &integral);
    control_diag.samples = 0;
    control_diag.left_counts = control_diag.right_counts = 0;
    control_diag.common_min = control_diag.common_max = 0;
    control_diag.left_reversals = control_diag.right_reversals = 0;
    __set_PRIMASK(irq);
    /* tick, enabled, samples, P/D balance, PI velocity, yaw, integral,
     * filtered speed x100, common min/max, wheel sums, PWM reversals. */
    snprintf(text, size,
             "DIAG,CTRL,%lu,%u,%lu,%d,%d,%d,%ld,%ld,%d,%d,%ld,%ld,%lu,%lu\n",
             (unsigned long)tick, snapshot.enabled, (unsigned long)snapshot.samples,
             snapshot.balance, snapshot.velocity, snapshot.yaw,
             (long)integral, (long)(filtered * 100.0f),
             snapshot.common_min, snapshot.common_max, snapshot.left_counts, snapshot.right_counts,
             (unsigned long)snapshot.left_reversals, (unsigned long)snapshot.right_reversals);
    /* Same tick/window as CTRL: all/command-free samples, enabled absolute wheel
     * travel, output-limit hits, common reversals, dt range, raw yaw sum, angle x100.
     * Limit hits include exact-boundary outputs; they do not prove clipping.
     * Raw gyro average is for bias diagnosis, never an absolute heading estimate. */
    snprintf(observations, observations_size,
             "DIAG,OBS,%lu,%lu,%lu,%ld,%ld,%lu,%lu,%lu,%lu,%lu,%ld,%ld,%ld\n",
             (unsigned long)tick, (unsigned long)observed.samples,
             (unsigned long)observed.command_free_samples, observed.left_travel, observed.right_travel,
             (unsigned long)observed.left_limit, (unsigned long)observed.right_limit,
             (unsigned long)observed.common_reversals, (unsigned long)observed.min_dt,
             (unsigned long)observed.max_dt, observed.yaw_raw_sum,
             (long)(observed.min_angle * 100.0f), (long)(observed.max_angle * 100.0f));
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
    int encoder_left, encoder_right;
    int balance_pwm, velocity_pwm, turn_pwm;
    u8 commanded_turn, motor_enabled;

    if (GPIO_Pin != MPU6050_Int_Pin)
        return;

    if (!Get_Angle(GET_Angle_Way)) {
        /* Do not publish a fresh timestamp or use a failed sensor frame.
         * Existing motion/calibration watchdogs stop on invalid samples. */
        /* Calibration may skip a short read failure; its watchdog and valid
         * sample timestamps still enforce the bounded gap. Motion keeps the
         * existing invalid-frame behaviour below. */
        angle_sample_valid = 0;
        rear_support_samples = 0;
        standing_samples = 0;
        return;
    }
    if (angle_sample_valid)
    {
        diagnostic_imu_dt = (uint32_t)(HAL_GetTick() - angle_last_update_ms);
        if (diagnostic_imu_dt > diagnostic_imu_max_dt)
            diagnostic_imu_max_dt = diagnostic_imu_dt;
    }
    angle_sample_valid = 1;
    angle_last_update_ms = HAL_GetTick();
    encoder_left = Read_Encoder(MOTOR_ID_ML);
    encoder_right = -Read_Encoder(MOTOR_ID_MR);
    diagnostic_left = encoder_left;
    diagnostic_right = encoder_right;
    Get_Velocity_Form_Encoder(encoder_left, encoder_right);
    if (Chassis_GyroActive()) {
        if (!HAL_GPIO_ReadPin(KEY1_GPIO_Port, KEY1_Pin)) {
            calibration_key_blocked = 1; calibration_key_release_ms = 0;
        }
        GyroCal_Observe(&gyro_cal, HAL_GetTick(), Angle_Balance, gyro_pitch_raw, gyro_yaw_raw,
            Stop_Flag && !calibration_active && !turn_state && !bluetooth_control_enabled &&
            (chassis_rest_state == 0 || chassis_rest_state == 2) &&
            !encoder_left && !encoder_right && !Move_X && !Move_Z && g_newcarstate == enSTOP &&
            HAL_GPIO_ReadPin(KEY1_GPIO_Port, KEY1_Pin) && !lower_power_flag && battery >= 9.6f);
        Gyro_Balance = gyro_pitch_raw - (gyro_cal.valid ? gyro_cal.pitch_bias : 0);
        Gyro_Turn = gyro_yaw_raw - (gyro_cal.valid ? gyro_cal.yaw_bias : 0);
        Stop_Flag = 1; Motor_Left = Motor_Right = 0; Set_Pwm(0, 0);
        record_control(0, 0, 0, 0, encoder_left, encoder_right);
        return;
    }

    if (Stop_Flag == 1 && chassis_rest_state != 3 &&
        Angle_Balance >= -39.0f && Angle_Balance <= -8.0f &&
        Gyro_Balance > -100.0f && Gyro_Balance < 100.0f &&
        Gyro_Turn > -82.0f && Gyro_Turn < 82.0f &&
        myabs(encoder_left) + myabs(encoder_right) < 10)
    {
        if (rear_support_samples < 80) rear_support_samples++;
    }
    else rear_support_samples = 0;

    if (calibration_on_imu(encoder_left, encoder_right)) {
        record_control(0, 0, 0, 0, encoder_left, encoder_right);
        return;
    }

    Chassis_BluetoothWatchdog();
    if (!turn_stance_failure(encoder_left, encoder_right) && chassis_rest_state == 0) {
        if (standing_samples < 100) standing_samples++;
    } else standing_samples = 0;
    turn_on_imu(encoder_left, encoder_right);

    balance_pwm = Balance_PD(Angle_Balance, Gyro_Balance);
    velocity_pwm = Velocity_PI(encoder_left, encoder_right);
    turn_pwm = Turn_PD(Gyro_Turn);

    if (chassis_rest_state == 1)
    {
        uint32_t elapsed = HAL_GetTick() - rest_started_ms;
        rest_last_update_ms = HAL_GetTick();
        if (Angle_Balance <= -1.2f && Angle_Balance <= rest_start_angle - 0.4f)
            rest_rear_samples++;
        else
            rest_rear_samples = 0;
        if (rest_rear_samples >= 3)
        {
            /* A small rear tilt is enough; the support catches the body. */
            chassis_rest_state = 2;
            Stop_Flag = 1;
            chassis_rest_event = 1;
        }
        else if (elapsed > 450U || Angle_Balance > 8.0f ||
                 myabs(encoder_left) + myabs(encoder_right) > 80 ||
                 lower_power_flag)
        {
            /* No rear tilt or excessive motion: keep balancing instead. */
            Chassis_CancelRest();
        }
        else
        {
            if (rest_target_offset > rest_offset_limit) rest_target_offset -= 0.20f;
            if (rest_target_offset < rest_offset_limit) rest_target_offset = rest_offset_limit;
            balance_pwm -= (int)(Balance_Kp / 100.0f * rest_target_offset);
        }
    }

    if (chassis_rest_state == 1)
    {
        Motor_ResetBalanceCompensation();
        /* Do not let the speed integrator turn a lean into a drive command. */
        Motor_Left = PWM_Limit(PWM_Ignore(balance_pwm), MOTOR_REST_PWM_LIMIT, -MOTOR_REST_PWM_LIMIT);
        Motor_Right = PWM_Limit(PWM_Ignore(balance_pwm), MOTOR_REST_PWM_LIMIT, -MOTOR_REST_PWM_LIMIT);
    }
    else
    {
        commanded_turn = turn_state == TURN_ROTATING || Move_Z != 0 ||
            g_newcarstate == enLEFT || g_newcarstate == enRIGHT ||
            g_newcarstate == enTLEFT || g_newcarstate == enTRIGHT;
        Motor_MixBalance(balance_pwm + velocity_pwm, turn_pwm, commanded_turn,
                         encoder_left, encoder_right, &Motor_Left, &Motor_Right);
    }

    if (chassis_rest_state == 0 && Pick_Up(Acceleration_Z, Angle_Balance, encoder_left, encoder_right))
        Stop_Flag = 1;
    if (!Chassis_GyroActive() && chassis_rest_state == 0 && Put_Down(Angle_Balance, encoder_left, encoder_right))
        Stop_Flag = 0;

    motor_enabled = Turn_Off(Angle_Balance, battery) == 0;
    if (motor_enabled)
        Set_Pwm(Motor_Left, Motor_Right);
    else {
        Motor_ResetBalanceCompensation();
    }
    record_control(motor_enabled, balance_pwm, velocity_pwm, turn_pwm, encoder_left, encoder_right);
}

/**************************************************************************
Function: Get angle
Input   : way：The algorithm of getting angle 1：DMP  2：kalman  3：Complementary filtering
Output  : none
函数功能：获取角度
入口参数：way：获取角度的算法 1：DMP  2：卡尔曼 3：互补滤波
返回  值：无
**************************************************************************/	
u8 Get_Angle(u8 way)
{ 
	float gyro_x,gyro_y,accel_x,accel_y,accel_z;
	float Accel_Y,Accel_Z,Accel_X,Accel_Angle_x,Accel_Angle_y,Gyro_X,Gyro_Z,Gyro_Y;
	u8 sample[14];
	if(way==1)                           //DMP的读取在数据采集中断读取，严格遵循时序要求  //The reading of DMP is interrupted during data collection, strictly following the timing requirements
	{
		Temperature=Read_Temperature();      //读取MPU6050内置温度传感器数据，近似表示主板温度。 //Read the data from the MPU6050 built-in temperature sensor, which approximately represents the motherboard temperature.
		Read_DMP();                      	 //读取加速度、角速度、倾角  //Read acceleration, angular velocity, and tilt angle
		Angle_Balance=Pitch;             	 //更新平衡倾角,前倾为正，后倾为负 //Update the balance tilt angle, with positive forward tilt and negative backward tilt
		Gyro_Balance=gyro[0];              //更新平衡角速度,前倾为正，后倾为负  //Update the balance angular velocity, with positive forward tilt and negative backward tilt
		Gyro_Turn=gyro[2];                 //更新转向角速度 //Update steering angular velocity
		Acceleration_Z=accel[2];           //更新Z轴加速度计 //Update Z-axis accelerometer
	}			
	else
	{
		/* One burst keeps every high/low byte and axis in the same sample. */
		if (i2cRead(devAddr >> 1, MPU6050_RA_ACCEL_XOUT_H, sizeof(sample), sample))
			return 0;
		Accel_X = Imu_SignedWord(sample);
		Accel_Y = Imu_SignedWord(sample + 2);
		Accel_Z = Imu_SignedWord(sample + 4);
		Temperature = (int)((36.53f + Imu_SignedWord(sample + 6) / 340.0f) * 10.0f);
		Gyro_X = Imu_SignedWord(sample + 8);
		Gyro_Y = Imu_SignedWord(sample + 10);
		Gyro_Z = Imu_SignedWord(sample + 12);
		Gyro_Balance=-Gyro_X;                            //更新平衡角速度 Update balance angular velocity
		accel_x=Accel_X/1671.84;
		accel_y=Accel_Y/1671.84;
		accel_z=Accel_Z/1671.84;
		gyro_x=Gyro_X/939.8;                              //陀螺仪量程转换 Gyroscope range conversion
		gyro_y=Gyro_Y/939.8;                              //陀螺仪量程转换 Gyroscope range conversion
		if(GET_Angle_Way==2)		  	
		{
			 Pitch= KF_X(accel_y,accel_z,-gyro_x)/PI*180;//卡尔曼滤波 Kalman filtering 
			 Roll = KF_Y(accel_x,accel_z,gyro_y)/PI*180;
		}
		else if(GET_Angle_Way==3) 
		{  
				Accel_Angle_x = atan2(Accel_Y,Accel_Z)*180/PI; //用Accel_Y和accel_y的参数得出的角度是一样的，只是边长不同 The angle obtained using Accel_Y and its parameters is the same, only the side length is different
				Accel_Angle_y = atan2(Accel_X,Accel_Z)*180/PI;
			
			 Pitch = -Complementary_Filter_x(Accel_Angle_x,Gyro_X/16.4);//互补滤波 Complementary filtering
			 Roll = -Complementary_Filter_y(Accel_Angle_y,Gyro_Y/16.4);
		}
		Angle_Balance=Pitch;                              //更新平衡倾角    Update the balance tilt angle
		Gyro_Turn=Gyro_Z;                                 //更新转向角速度  Update steering angular velocity
		Acceleration_Z=Accel_Z;                           //更新Z轴加速度计 Update Z-axis accelerometer
	}
    gyro_pitch_raw = Gyro_Balance; gyro_yaw_raw = Gyro_Turn;
    Gyro_Balance = gyro_pitch_raw - (gyro_cal.valid ? gyro_cal.pitch_bias : 0);
    Gyro_Turn = gyro_yaw_raw - (gyro_cal.valid ? gyro_cal.yaw_bias : 0);
	return 1;

}


/**************************************************************************
Function: Check whether the car is picked up
Input   : Acceleration：Z-axis acceleration；Angle：The angle of balance；encoder_left：Left encoder count；encoder_right：Right encoder count
Output  : 1：picked up  0：No action
函数功能：检测小车是否被拿起
入口参数：Acceleration：z轴加速度；Angle：平衡的角度；encoder_left：左编码器计数；encoder_right：右编码器计数
返回  值：1:小车被拿起  0：小车未被拿起
**************************************************************************/
int Pick_Up(float Acceleration,float Angle,int encoder_left,int encoder_right)
{ 		   
	 static u16 flag,count0,count1,count2;
	 if(flag==0)                                                      //第一步  Step 1
	 {
			if(myabs(encoder_left)+myabs(encoder_right)<50)               //条件1，小车接近静止 Condition 1: The car is approaching a standstill
			count0++;
			else 
			count0=0;		
			if(count0>10)				
			flag=1,count0=0; 
	 } 
	 if(flag==1)                                                      //进入第二步 Go to step 2
	 {
			if(++count1>200)       count1=0,flag=0;                       //超时不再等待2000ms，返回第一步 No more waiting for 2000ms after timeout, return to the first step
			if(Acceleration>22000&&(Angle>(-20+Mid_Angle))&&(Angle<(20+Mid_Angle)))   //条件2，小车是在0度附近被拿起 Condition 2, the car is picked up near 0 degrees
			flag=2; 
	 } 
	 if(flag==2)                                                       //第三步 Step 3
	 {
		  if(++count2>100)       count2=0,flag=0;                        //超时不再等待1000ms Timeout no longer waits 1000ms
	    if(myabs(encoder_left+encoder_right)>50)                       //条件3，小车的轮胎因为正反馈达到最大的转速    Condition 3: The tires of the car reach their maximum speed due to positive feedback
      {
				flag=0;                                                                                     
				return 1;                                                    //检测到小车被拿起 Detected the car being picked up
			}
	 }
	return 0;
}
/**************************************************************************
Function: Check whether the car is lowered
Input   : The angle of balance；Left encoder count；Right encoder count
Output  : 1：put down  0：No action
函数功能：检测小车是否被放下
入口参数：平衡角度；左编码器读数；右编码器读数
返回  值：1：小车放下   0：小车未放下
**************************************************************************/
int Put_Down(float Angle,int encoder_left,int encoder_right)
{ 		   
	 static u16 flag;//,count;	 
	 if(Stop_Flag==0)                     //防止误检    Prevent false positives   
			return 0;	                 
	 if(flag==0)                                               
	 {
			if(Angle>(-10+Mid_Angle)&&Angle<(10+Mid_Angle)&&encoder_left==0&&encoder_right==0) //条件1，小车是在0度附近的 Condition 1, the car is around 0 degrees
			flag=1; 
	 } 
	 if(flag==1)                                               
	 {
//		  if(++count>50)                     //超时不再等待 500ms  Timeout no longer waits 500ms
//		  {
//				count=0;flag=0;
//		  }
		 //增加灵敏性 Increase sensitivity
	    if((encoder_left>3&&encoder_left<40)||(encoder_right>3&&encoder_right<40)) //条件2，小车的轮胎在未上电的时候被人为转动  Condition 2: The tires of the car are manually rotated when not powered on
      {
				flag=0;
				return 1;                         //检测到小车被放下 Detected that the car has been lowered
			}
	 }
	return 0;
}

