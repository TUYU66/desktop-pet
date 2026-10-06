#include "chassis_link.h"
#include "AllHeader.h"
#include "link_frame.h"
#include "calibration_deadline.h"
#include "tuning_values.h"
#include <stdio.h>
#include <string.h>

/* Both peers require wire v2 CRC frames. Never execute legacy/raw commands. */
#define CHASSIS_LINE_CAPACITY 128
#define CHASSIS_RX_CAPACITY 128

static char line[CHASSIS_LINE_CAPACITY];
static uint8_t line_length;
static uint8_t dropping_line;
static uint8_t rx_buffer[CHASSIS_RX_CAPACITY];
static volatile uint8_t rx_head;
static volatile uint8_t rx_tail;
static volatile uint8_t rx_overflow;
static uint32_t last_battery_report_ms;
static unsigned int battery_sequence;
static unsigned int turn_sequence;
static uint32_t last_diagnostic_ms;
static uint32_t last_baseline_report_ms;
static uint32_t last_turn_diagnostic_ms;
static char last_turn_result[48];
static unsigned int last_turn_sequence;
static uint32_t last_turn_result_ms;
static unsigned int posture_sequence, posture_result_sequence;
static uint8_t posture_standing;
static uint8_t posture_bluetooth; /* 0=posture, 1=enable, 2=off/resting, 3=off/settle then rest, 4=off/keep standing */
/* Foreground session state: repeated ON must not overwrite the original pose.
 * A failed return-to-rest retains its goal for an explicit OFF retry. */
static uint8_t bluetooth_return_to_rest, bluetooth_enable_from_rest;
static uint32_t posture_started_ms, posture_result_ms;
static char posture_result[48];
/* Lifetime counts: crc, framing, overrun, ring full, frame, parity, noise, resync. */
static volatile uint32_t link_errors[8];
static uint32_t last_link_error_total;
static uint32_t last_link_report_ms;

static const char *turn_error_name(uint8_t reason)
{
    switch (reason)
    {
        case 1: return "TILT";
        case 2: return "STOPPED";
        case 3: return "LOW_BATT";
        case 4: return "IMU_TIMEOUT";
        case 5: return "TURN_TIMEOUT";
        case 6: return "REST_FAILED";
        case 7: return "REST_TIMEOUT";
        case 8: return "STAND_TIMEOUT";
        case 9: return "NOT_STABLE";
        case 10: return "WRONG_DIRECTION";
        case 11: return "TURN_STALLED";
        default: return "UNKNOWN";
    }
}

static const char *start_error_name(u8 result)
{
    switch (result) {
        case 1: return "NOT_READY";
        case 2: return "LOW_BATT";
        case 3: return "ANGLE";
        case 4: return "NOT_STANDING";
        default: return "BUSY";
    }
}

static void send_line(const char *message)
{
    /* Foreground-only TX; keep the frame off the 1 KB main/IRQ stack. */
    static char frame[208];
    size_t length = strcspn(message, "\r\n");
    size_t i;
    int written;
    if (length == 0 || length > sizeof(frame) - 8) return;
    written = snprintf(frame, sizeof(frame), "@%.*s*%04X\n", (int)length,
                       message, (unsigned int)link_crc16(message, length));
    if (written <= 0 || (size_t)written >= sizeof(frame)) return;
    for (i = 0; i < (size_t)written; ++i)
    {
        while (!LL_USART_IsActiveFlag_TXE(USART2)) {}
        LL_USART_TransmitData8(USART2, (uint8_t)frame[i]);
    }
}

static uint8_t parse_sequence(const char *digits, unsigned int *sequence)
{
    unsigned long value = 0;
    if (*digits == '\0') return 0;
    while (*digits)
    {
        if (*digits < '0' || *digits > '9') return 0;
        value = value * 10 + (unsigned long)(*digits - '0');
        if (value > 65535UL) return 0;
        digits++;
    }
    *sequence = (unsigned int)value;
    return 1;
}

static void stand_reply(unsigned int sequence, const char *error)
{
    char response[40];
    if (error)
        snprintf(response, sizeof(response), "ERR,%u,%s\n", sequence, error);
    else
        snprintf(response, sizeof(response), "ACK,%u\n", sequence);
    send_line(response);
}

/* Rejections are terminal results too. Recover a lost ERR by querying the
 * same ID, without retransmitting a physical movement command. */
static void turn_reject(unsigned int sequence, const char *error)
{
    last_turn_sequence = sequence;
    last_turn_result_ms = HAL_GetTick();
    snprintf(last_turn_result, sizeof(last_turn_result), "ERR,%u,%s\n", sequence, error);
    send_line(last_turn_result);
}

/* pose: 0=confirmed standing, 1=confirmed seated, 2=not confirmed.
 * Old peers receive no fake posture completion; updated ESP32 parses all four fields. */
static void bluetooth_done(unsigned int sequence, u8 enabled, u8 pose)
{
    posture_result_sequence = sequence;
    posture_result_ms = HAL_GetTick();
    snprintf(posture_result, sizeof(posture_result), "BT_DONE,%u,%u,%u\n",
             sequence, (unsigned int)enabled, (unsigned int)pose);
    stand_reply(sequence, 0);
    send_line(posture_result);
}

/* Strict bounded numbers, independent of embedded scanf overflow behaviour. */
static u8 calibration_fields(const char *p, long *values, unsigned int count)
{
    unsigned int i;
    for (i = 0; i < count; ++i) {
        unsigned long value = 0;
        unsigned long limit = i == 4 ? 2147483647UL : 65535UL;
        u8 negative = *p == '-';
        if (negative) p++;
        if (*p < '0' || *p > '9') return 0;
        do {
            unsigned long digit = (unsigned long)(*p++ - '0');
            if (value > (limit - digit) / 10UL) return 0;
            value = value * 10UL + digit;
        } while (*p >= '0' && *p <= '9');
        values[i] = negative ? -(long)value : (long)value;
        if (i + 1 == count) return *p == 0;
        if (*p++ != ',') return 0;
    }
    return 0;
}

static unsigned int calibration_last_sequence, calibration_last_session;
static long calibration_last_wheel, calibration_last_pwm;

static void calibration_reply(unsigned int sequence, u8 error)
{
    static char state[192];
    char reply[32];
    Chassis_FormatCalibration(state, sizeof(state));
    send_line(state); /* State precedes ACK; the ESP32 never confirms a guessed state. */
    snprintf(reply, sizeof(reply), "CALACK,%u,%u\n", sequence, error);
    send_line(reply);
}

static void handle_calibration(void)
{
    unsigned int seq;
    u8 error;
    if (strncmp(line, "CAL,ARM,", 8) == 0) {
        if (!parse_sequence(line + 8, &seq) || !seq) return;
        if (posture_sequence || Chassis_TurnBusy()) { calibration_reply(seq, 5); return; }
        error = Chassis_CalibrationCommand(1, seq, 0, 0, 0);
        if (!error) {
            calibration_last_session = seq;
            calibration_last_sequence = 0;
        }
        calibration_reply(seq, error);
    } else if (strncmp(line, "CAL,EXIT,", 9) == 0) {
        if (!parse_sequence(line + 9, &seq) || !seq) return;
        error = Chassis_CalibrationCommand(3, 0, 0, 0, 0);
        calibration_reply(seq, error);
    } else if (strncmp(line, "CAL,SET,", 8) == 0) {
        long args[5];
        if (!calibration_fields(line + 8, args, 5) || args[0] <= 0 || args[1] <= 0) return;
        seq = (unsigned int)args[1];
        if (args[0] != calibration_last_session) { calibration_reply(seq, 6); return; }
        if (args[2] < 1 || args[2] > 2 || args[3] < -MOTOR_BALANCE_PWM_LIMIT || args[3] > MOTOR_BALANCE_PWM_LIMIT) {
            calibration_reply(seq, 7); return;
        }
        if (seq == calibration_last_sequence) {
            /* Duplicate requests never renew a motor lease or restart a pulse. */
            calibration_reply(seq, args[2] == calibration_last_wheel && args[3] == calibration_last_pwm ? 0 : 7);
            return;
        }
        if (calibration_last_sequence && (uint16_t)(seq - calibration_last_sequence) > 32767U) {
            calibration_reply(seq, 6); return;
        }
        /* Sample tick is the operator's observed STM32 clock (31-bit modulo).
         * Reject late queued pulses rather than starting a wheel after Stop. */
        if (args[4] < 0 || (args[3] != 0 && !Calibration_SampleFresh(HAL_GetTick(), (uint32_t)args[4]))) {
            calibration_reply(seq, 6); return;
        }
        error = Chassis_CalibrationCommand(2, (unsigned int)args[0], (u8)args[2], (int)args[3], (uint32_t)args[4]);
        if (!error) {
            calibration_last_sequence = seq;
            calibration_last_wheel = args[2]; calibration_last_pwm = args[3];
        }
        calibration_reply(seq, error);
    }
}

static unsigned int tuning_revision = 1;
static long tuning_previous[TUNING_COUNT];
static u8 tuning_has_previous;
static long tuning_known[TUNING_COUNT];
static u8 tuning_known_valid;
static unsigned int tuning_known_gyro_revision;
static char tuning_last_command[CHASSIS_LINE_CAPACITY];
static unsigned int tuning_last_revision;
static uint32_t last_live_ms;

static long tuning_scaled(float value, float scale)
{
    float scaled = value * scale;
    return (long)(scaled >= 0 ? scaled + 0.5f : scaled - 0.5f);
}

static void tuning_read(long *p)
{
    p[0]=tuning_scaled(Mid_Angle,1000.0f); p[1]=tuning_scaled(Balance_Kp,100.0f);
    p[2]=tuning_scaled(Balance_Kd,100.0f); p[3]=tuning_scaled(Velocity_Kp,100.0f);
    p[4]=tuning_scaled(Velocity_Ki,100.0f); p[5]=tuning_scaled(myTurn_Kd,100.0f);
}

/* Caller masks IRQs. Legacy Bluetooth gain changes must invalidate stale forms too. */
static void tuning_refresh(long *p)
{
    tuning_read(p);
    if (tuning_known_valid && (memcmp(p,tuning_known,sizeof(tuning_known)) ||
        tuning_known_gyro_revision != Chassis_GyroRevision())) {
        tuning_revision=tuning_revision>=2147483646U?1:tuning_revision+1;
        tuning_has_previous=0;
    }
    memcpy(tuning_known,p,sizeof(tuning_known));
    tuning_known_valid=1;
    tuning_known_gyro_revision=Chassis_GyroRevision();
}

static void tuning_reply(unsigned int seq, unsigned int error)
{
    static char text[192];
    long p[TUNING_COUNT];
    unsigned int writable;
    uint32_t irq;
    unsigned int captured;
    do {
        irq=__get_PRIMASK();
        __disable_irq();
        tuning_refresh(p);
        writable=!posture_sequence && Chassis_TuningWritable();
        __set_PRIMASK(irq);
        captured=Chassis_FormatGyro(text,sizeof(text),tuning_revision);
    } while (captured!=tuning_known_gyro_revision);
    send_line(text);
    /* Send the ACK last, after gyro state is available to the ESP32. */
    snprintf(text,sizeof(text),"TSTATE,%u,%u,%lu,%u,%u,%u,%ld,%ld,%ld,%ld,%ld,%ld\n",
             seq,error,(unsigned long)(HAL_GetTick()&0x7FFFFFFFUL),tuning_revision,
             writable,(unsigned int)tuning_has_previous,p[0],p[1],p[2],p[3],p[4],p[5]);
    send_line(text);
}

static void handle_tuning(void)
{
    long a[9], current[TUNING_COUNT];
    unsigned int seq, i, error=0;
    uint32_t irq;
    u8 set=strncmp(line,"TUNE,SET,",9)==0;
    u8 undo=strncmp(line,"TUNE,UNDO,",10)==0;
    u8 check=strncmp(line,"TUNE,CHECK,",11)==0;
    u8 gyro_start=strncmp(line,"TUNE,GSTART,",12)==0;
    u8 gyro_cancel=strncmp(line,"TUNE,GCANCEL,",13)==0;
    u8 gyro_clear=strncmp(line,"TUNE,GCLEAR,",12)==0;
    u8 gyro=gyro_start || gyro_cancel || gyro_clear;
    if (strncmp(line,"TUNE,GET,",9)==0) {
        if (parse_sequence(line+9,&seq) && seq) tuning_reply(seq,0);
        return;
    }
    if (!set && !undo && !check && !gyro) return;
    if (!Tuning_Parse(line+(set?9:undo?10:check?11:gyro_cancel?13:12),a,set?9:3) || a[0]<1 || a[0]>65535) return;
    seq=(unsigned int)a[0];
    irq=__get_PRIMASK();
    __disable_irq();
    tuning_refresh(current);
    if (!strcmp(line,tuning_last_command)) {
        error=tuning_revision==tuning_last_revision?0:4;
        __set_PRIMASK(irq);
        tuning_reply(seq,error); return;
    }
    if (a[1] != tuning_revision) error=4;
    else if (a[2]<0 || !Calibration_SampleFresh(HAL_GetTick(),(uint32_t)a[2])) error=5;
    else if (posture_sequence || (!gyro_cancel && !Chassis_TuningWritable())) error=2;
    else if (set && !Tuning_ValuesValid(a+3)) error=3;
    else if (undo && !tuning_has_previous) error=6;
    if (!error && gyro) {
        error=Chassis_GyroCommand(gyro_start?1:gyro_cancel?2:3);
        tuning_refresh(current);
        if (!error && !gyro_clear) {
            /* Fence delayed start/cancel packets even though bias is not yet changed. */
            tuning_revision=tuning_revision>=2147483646U?1:tuning_revision+1;
            tuning_has_previous=0;
        }
    }
    if (!error && !check && !gyro) {
        for (i=0;i<TUNING_COUNT;++i) {
            long target=set?a[i+3]:tuning_previous[i];
            a[i+3]=target;
            tuning_previous[i]=current[i];
        }
        Mid_Angle=a[3]/1000.0f; Balance_Kp=a[4]/100.0f; Balance_Kd=a[5]/100.0f;
        Velocity_Kp=a[6]/100.0f; Velocity_Ki=a[7]/100.0f; myTurn_Kd=a[8]/100.0f;
        tuning_read(tuning_known);
        PID_ResetVelocity(); Motor_ResetBalanceCompensation();
        tuning_has_previous=1;
        tuning_revision=tuning_revision>=2147483646U?1:tuning_revision+1;
    }
    __set_PRIMASK(irq);
    if (!error) {
        strcpy(tuning_last_command,line); tuning_last_revision=tuning_revision;
    }
    tuning_reply(seq,error);
}

static void handle_line(void)
{
    if (strncmp(line, "TUNE,", 5) == 0) { handle_tuning(); return; }
    if (strncmp(line, "CAL,", 4) == 0) { handle_calibration(); return; }
    if ((Chassis_CalibrationActive() || Chassis_GyroActive()) &&
        (strncmp(line, "STAND,", 6) == 0 || strncmp(line, "REST,", 5) == 0 ||
         strncmp(line, "TURN,", 5) == 0 || strncmp(line, "BT,", 3) == 0)) {
        unsigned int seq;
        const char *last = strrchr(line, ',');
        if (last && parse_sequence(last + 1, &seq) && seq != 0) {
            if (strncmp(line, "TURN,", 5) == 0) turn_reject(seq, "BUSY");
            else stand_reply(seq, "BUSY");
        }
        return;
    }
    if (strcmp(line, "PING") == 0)
    {
        send_line("PONG,1\n");
    }
    else if (strcmp(line, "STATUS") == 0)
    {
        char response[48];
        long battery_mv = battery_sample_valid ? (long)(battery * 1000.0f + 0.5f) : -1L;
        snprintf(response, sizeof(response), "STATUS,1,%ld,%u,%u\n",
                 battery_mv, (unsigned int)Stop_Flag, (unsigned int)lower_power_flag);
        send_line(response);
    }
    else if (strncmp(line, "TURN_RESULT,", 12) == 0)
    {
        unsigned int sequence;
        if (!parse_sequence(line + 12, &sequence) || sequence == 0) return;
        if (sequence == last_turn_sequence && last_turn_result[0] &&
            (uint32_t)(HAL_GetTick() - last_turn_result_ms) < 60000U)
            send_line(last_turn_result);
        else if (sequence == turn_sequence)
            stand_reply(sequence, 0);
        else {
            /* A lost TURN or STM32 reboot must not cause a silent 50 s wait.
             * This confirms only absence of a cached/active ID, never no movement. */
            char response[40];
            snprintf(response, sizeof(response), "TURN_UNKNOWN,%u\n", sequence);
            send_line(response);
        }
    }
    else if (strncmp(line, "BT,", 3) == 0 && (line[3] == '0' || line[3] == '1') && line[4] == ',')
    {
        unsigned int sequence;
        u8 enabled = line[3] == '1';
        u8 seated;
        if (!parse_sequence(line + 5, &sequence) || sequence == 0) return;
        if (sequence == posture_result_sequence) posture_result[0] = 0;
        if (!enabled) {
            /* Revoke phone motion before any busy/posture check or reply. */
            Chassis_SetBluetooth(0);
            Bluetooth_DiscardInput();
            if (posture_sequence && posture_bluetooth == 1) {
                /* An OFF during an unfinished ON must never enable later. */
                bluetooth_return_to_rest = bluetooth_enable_from_rest;
                posture_result_sequence = posture_sequence;
                posture_result_ms = HAL_GetTick();
                snprintf(posture_result, sizeof(posture_result), "BT_ABORT,%u,STAND\n", posture_sequence);
                send_line(posture_result);
                posture_sequence = posture_bluetooth = 0;
            }
        }
        if (posture_sequence || Chassis_TurnBusy()) { stand_reply(sequence, "BUSY"); return; }
        seated = Chassis_IsSeated();
        if (enabled) {
            if (chassis_rest_state == 3) { stand_reply(sequence, "FAULT"); return; }
            if (!Battery_IsReady(0) || !angle_sample_valid ||
                (uint32_t)(HAL_GetTick() - angle_last_update_ms) > 150U) {
                stand_reply(sequence, "NOT_READY"); return;
            }
            if (lower_power_flag || battery < 9.6f) { stand_reply(sequence, "LOW_BATT"); return; }
            if (Chassis_BluetoothEnabled()) {
                /* Idempotent enable: preserve a seated-origin session. */
                if (Stop_Flag || chassis_rest_state != 0) {
                    Chassis_SetBluetooth(0);
                    Bluetooth_DiscardInput();
                    stand_reply(sequence, "FAULT");
                } else bluetooth_done(sequence, 1, 0);
                return;
            }
            if ((Stop_Flag || chassis_rest_state != 0) && !seated) {
                stand_reply(sequence, "NOT_STANDING"); return;
            }
            bluetooth_enable_from_rest = seated;
            Chassis_SetBluetooth(0);
            Bluetooth_DiscardInput();
            if (seated) {
                u8 result = Chassis_StartBalance();
                if (result) { stand_reply(sequence, start_error_name(result)); return; }
            }
            posture_bluetooth = 1;
            posture_standing = 1;
        } else if (seated) {
            /* Already resting: no stand/sit cycle, including repeated OFF. */
            bluetooth_return_to_rest = 0;
            bluetooth_done(sequence, 0, 1);
            return;
        } else if (bluetooth_return_to_rest) {
            if (Stop_Flag || chassis_rest_state != 0) {
                posture_result_sequence = sequence;
                posture_result_ms = HAL_GetTick();
                snprintf(posture_result, sizeof(posture_result), "BT_ABORT,%u,REST\n", sequence);
                stand_reply(sequence, 0);
                send_line(posture_result);
                return;
            }
            posture_bluetooth = 3; /* Stop phone motion, settle, then sit. */
            posture_standing = 1;
        } else if (!Stop_Flag && chassis_rest_state == 0) {
            posture_bluetooth = 4; /* Keep balance active; never call REST. */
            posture_standing = 1;
        } else {
            bluetooth_done(sequence, 0, 2); /* Control off; no posture claim. */
            return;
        }
        posture_sequence = sequence;
        posture_started_ms = HAL_GetTick();
        posture_result[0] = 0;
        stand_reply(sequence, 0);
    }
    else if (strncmp(line, "STAND,", 6) == 0)
    {
        unsigned int sequence;
        uint32_t battery_age;
        if (!parse_sequence(line + 6, &sequence)) return;
        if (posture_sequence) { stand_reply(sequence, "BUSY"); return; }
        if (Chassis_BluetoothEnabled()) stand_reply(sequence, "BT_ACTIVE");
        else if (Chassis_TurnBusy()) stand_reply(sequence, "BUSY");
        else if (!Battery_IsReady(&battery_age))
        {
            char diagnostic[48];
            snprintf(diagnostic, sizeof(diagnostic), "DIAG,BAT,%u,%lu\n",
                     (unsigned int)battery_sample_valid, (unsigned long)battery_age);
            send_line(diagnostic);
            stand_reply(sequence, "NOT_READY");
        }
        else if (lower_power_flag || battery < 9.6f)
            stand_reply(sequence, "LOW_BATT");
        else
        {
            /* Same startup path as KEY1; ISR still enforces the angle limit. */
            u8 result = Chassis_StartBalance();
            if (result) { stand_reply(sequence, start_error_name(result)); return; }
            posture_sequence = sequence;
            posture_standing = 1;
            posture_bluetooth = 0;
            posture_started_ms = HAL_GetTick();
            posture_result[0] = 0;
            stand_reply(sequence, 0);
        }
    }
    else if (strncmp(line, "REST,", 5) == 0)
    {
        unsigned int sequence;
        u8 result;
        if (!parse_sequence(line + 5, &sequence)) return;
        if (posture_sequence) { stand_reply(sequence, "BUSY"); return; }
        if (Chassis_BluetoothEnabled()) { stand_reply(sequence, "BT_ACTIVE"); return; }
        if (Chassis_TurnBusy())
        {
            stand_reply(sequence, "BUSY");
            return;
        }
        result = Chassis_BeginRest();
        if (result == 1) stand_reply(sequence, "NOT_READY");
        else if (result == 2) stand_reply(sequence, "LOW_BATT");
        else if (result == 3) stand_reply(sequence, "ANGLE");
        else if (result == 4) stand_reply(sequence, "NOT_STANDING");
        else if (result == 5) stand_reply(sequence, "FAULT");
        else if (result == 6) stand_reply(sequence, "BUSY");
        else {
            posture_sequence = sequence;
            posture_standing = 0;
            posture_bluetooth = 0;
            posture_started_ms = HAL_GetTick();
            posture_result[0] = 0;
            stand_reply(sequence, 0);
        }
    }
    else if (strncmp(line, "TURN,", 5) == 0 && strlen(line) >= 8 && line[6] == ',')
    {
        unsigned int sequence;
        u8 direction = 0;
        u8 result;
        char received[40];
        if (!parse_sequence(line + 7, &sequence) || sequence == 0) return;
        if (line[5] == 'L') direction = 1;
        else if (line[5] == 'R') direction = 2;
        else if (line[5] == 'B') direction = 3;
        if (direction == 0) return;
        /* Receipt only: never advertise motion completion from this diagnostic. */
        snprintf(received, sizeof(received), "DIAG,TURN_REQ,%u,%u\n", sequence, (unsigned int)direction);
        send_line(received);
        if (posture_sequence) { turn_reject(sequence, "BUSY"); return; }
        if (Chassis_BluetoothEnabled()) { turn_reject(sequence, "BT_ACTIVE"); return; }
        /* ESP32 may reboot and reuse a sequence. A newly submitted TURN
         * invalidates an old result with that ID, even if it is rejected. */
        if (sequence == last_turn_sequence) last_turn_result[0] = '\0';
        result = Chassis_BeginTurn(direction);
        if (result == 1) turn_reject(sequence, "NOT_READY");
        else if (result == 2) turn_reject(sequence, "LOW_BATT");
        else if (result == 3) turn_reject(sequence, "ANGLE");
        else if (result == 4) turn_reject(sequence, "NOT_STANDING");
        else if (result == 5) turn_reject(sequence, "FAULT");
        else if (result == 6) turn_reject(sequence, "BUSY");
        else
        {
            turn_sequence = sequence;
            stand_reply(sequence, 0);
        }
    }
    else if (strncmp(line, "POSTURE_RESULT,", 15) == 0) {
        unsigned int sequence;
        if (!parse_sequence(line + 15, &sequence)) return;
        if (sequence == posture_result_sequence && posture_result[0] &&
            (uint32_t)(HAL_GetTick() - posture_result_ms) < 60000U) send_line(posture_result);
        else if (sequence == posture_sequence) stand_reply(sequence, 0);
    }
}

void ChassisLink_Init(void)
{
    MX_USART2_UART_Init();
    rx_head = 0;
    rx_tail = 0;
    rx_overflow = 0;
    line_length = 0;
    dropping_line = 1;
    last_baseline_report_ms = HAL_GetTick() - 10000U;
    last_battery_report_ms = HAL_GetTick();
    battery_sequence = 0;
    turn_sequence = 0;
    last_turn_sequence = 0;
    last_turn_result[0] = '\0';
    posture_sequence = posture_bluetooth = 0;
    bluetooth_return_to_rest = bluetooth_enable_from_rest = 0;
    posture_result_sequence = 0;
    posture_result[0] = 0;
    {
        unsigned int i;
        for (i = 0; i < 8; ++i) link_errors[i] = 0;
    }
    last_link_error_total = 0;
    last_link_report_ms = HAL_GetTick();
    LL_USART_EnableIT_RXNE(USART2);
}

void ChassisLink_OnRxInterrupt(void)
{
    /* STM32F1 clears RX/error flags by reading SR then DR. Read once so
     * clearing an old error cannot accidentally consume a subsequent byte. */
    uint32_t status = USART2->SR;
    if (status & (USART_SR_RXNE | USART_SR_ORE | USART_SR_FE | USART_SR_NE | USART_SR_PE))
    {
        uint8_t ch = (uint8_t)USART2->DR;
        uint8_t next = (uint8_t)((rx_head + 1) % CHASSIS_RX_CAPACITY);
        if (status & (USART_SR_ORE | USART_SR_FE | USART_SR_NE | USART_SR_PE))
        {
            if (status & USART_SR_ORE) link_errors[2]++;
            if (status & USART_SR_FE) link_errors[4]++;
            if (status & USART_SR_PE) link_errors[5]++;
            if (status & USART_SR_NE) link_errors[6]++;
            rx_overflow = 1;
            return;
        }
        if (next != rx_tail)
        {
            rx_buffer[rx_head] = ch;
            rx_head = next;
        }
        else
        {
            link_errors[3]++;
            rx_overflow = 1;
        }
    }
}

void ChassisLink_Poll(void)
{
    /* Bound work per foreground iteration so Bluetooth still gets service. */
    uint8_t remaining = 32;
    Chassis_CalibrationWatchdog();
    Chassis_GyroWatchdog();
    Chassis_BluetoothWatchdog();
    {
        u8 fault = Chassis_MotionWatchdog();
        if (fault == 1) send_line("FAULT,IMU_TIMEOUT\n");
    }
    Chassis_RestWatchdog();
    Chassis_TurnWatchdog();
    if (posture_sequence) {
        uint8_t result = Chassis_PostureResult(posture_standing);
        if (posture_bluetooth == 3 && result == 1) {
            /* Let velocity feedback arrest phone motion before leaning back. */
            if (Chassis_BeginRest() == 0) {
                posture_bluetooth = 2;
                posture_standing = 0;
                result = 0;
            } else result = 2;
        }
        if (posture_bluetooth == 3 && (uint32_t)(HAL_GetTick() - posture_started_ms) >= 8000U)
            result = 2;
        if (result || (uint32_t)(HAL_GetTick() - posture_started_ms) >= 10000U) {
            posture_result_sequence = posture_sequence;
            posture_result_ms = HAL_GetTick();
            if (posture_bluetooth) {
                if (result == 1 && posture_bluetooth == 1) {
                    Bluetooth_DiscardInput();
                    if (Chassis_SetBluetooth(1)) result = 2;
                    else bluetooth_return_to_rest = bluetooth_enable_from_rest;
                }
                if (result == 1) {
                    u8 rested = posture_bluetooth == 2;
                    if (posture_bluetooth != 1) bluetooth_return_to_rest = 0;
                    snprintf(posture_result, sizeof(posture_result), "BT_DONE,%u,%u,%u\n",
                             posture_sequence, (unsigned int)Chassis_BluetoothEnabled(), (unsigned int)rested);
                } else {
                    Chassis_SetBluetooth(0);
                    Bluetooth_DiscardInput();
                    snprintf(posture_result, sizeof(posture_result), "BT_ABORT,%u,%s\n",
                             posture_sequence, (posture_bluetooth == 1 || posture_bluetooth == 4) ? "STAND" : "REST");
                }
            } else
                snprintf(posture_result, sizeof(posture_result), "POSTURE_%s,%u\n",
                         result == 1 ? "DONE" : "ABORT", posture_sequence);
            posture_sequence = 0;
            posture_bluetooth = 0;
            send_line(posture_result);
        }
    }
    if (chassis_turn_event)
    {
        char response[48];
        uint8_t event = chassis_turn_event;
        uint8_t reason = chassis_turn_error;
        chassis_turn_event = 0;
        chassis_turn_error = 0;
        if (event == 1)
            snprintf(response, sizeof(response), "TURN_DONE,%u\n", turn_sequence);
        else
            snprintf(response, sizeof(response), "TURN_ABORT,%u,%s\n", turn_sequence,
                     (reason == 9 && Chassis_TurnFailurePhase() == 4)
                         ? "SETTLE_AFTER" : turn_error_name(reason));
        /* Cache before TX so a lost terminal reply can be queried without
         * repeating the motor command. Foreground owns this cache. */
        last_turn_sequence = turn_sequence;
        last_turn_result_ms = HAL_GetTick();
        snprintf(last_turn_result, sizeof(last_turn_result), "%s", response);
        send_line(response);
        {
            static char stable[192], timing[96];
            Chassis_FormatTurnDiagnostics(turn_sequence, stable, sizeof(stable), timing, sizeof(timing));
            send_line(stable);
            send_line(timing);
        }
        turn_sequence = 0;
    }
    if (chassis_rest_event)
    {
        uint8_t event = chassis_rest_event;
        chassis_rest_event = 0;
        if (event == 1) send_line("REST_DONE\n");
        else if (event == 2) send_line("REST_ABORT\n");
        else if (event == 3) send_line("REST_FAULT\n");
    }
    if (rx_overflow)
    {
        uint32_t irq = __get_PRIMASK();
        __disable_irq();
        rx_overflow = 0;
        rx_tail = rx_head;
        __set_PRIMASK(irq);
        line_length = 0;
        dropping_line = 1;
    }
    while (remaining-- && rx_tail != rx_head)
    {
        char ch = (char)rx_buffer[rx_tail];
        rx_tail = (uint8_t)((rx_tail + 1) % CHASSIS_RX_CAPACITY);
        if (ch == '@')
        {
            if (!dropping_line && line_length) link_errors[7]++;
            line_length = 0;
            dropping_line = 0;
        }
        else if (ch == '\n')
        {
            if (!dropping_line && line_length)
            {
                int decoded = link_decode(line, line_length);
                if (decoded == 1) handle_line();
                else if (decoded == -1) link_errors[0]++;
                else link_errors[1]++;
            }
            line_length = 0;
            dropping_line = 1;
        }
        else if (!dropping_line)
        {
            if (line_length < sizeof(line) - 1)
                line[line_length++] = ch;
            else
            {
                dropping_line = 1;
                link_errors[1]++;
            }
        }
    }

    if ((uint32_t)(HAL_GetTick() - last_link_report_ms) >= 5000U)
    {
        static char report[160];
        uint32_t counts[8], total = 0;
        unsigned int i;
        uint32_t irq = __get_PRIMASK();
        __disable_irq();
        for (i = 0; i < 8; ++i) counts[i] = link_errors[i];
        __set_PRIMASK(irq);
        for (i = 0; i < 8; ++i) total += counts[i];
        last_link_report_ms = HAL_GetTick();
        if (total != last_link_error_total) {
            snprintf(report, sizeof(report), "DIAG,LINK,COUNTS,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu\n",
                     (unsigned long)counts[0], (unsigned long)counts[1], (unsigned long)counts[2],
                     (unsigned long)counts[3], (unsigned long)counts[4], (unsigned long)counts[5],
                     (unsigned long)counts[6], (unsigned long)counts[7]);
            send_line(report);
            last_link_error_total = total;
        }
    }

    /* Repeat identity/config for late captures and ESP32 restarts.
     * Capabilities: mix=1, PI reset=2, BT posture=4, observations=8, manual calibration=16, measured compensation=32, BT session posture=64, unknown turn reply=128, tuning/live=256, manual gyro zero=512, dedicated support rise=1024.
     * Revision marks source capabilities, not physical acceptance. */
    if ((uint32_t)(HAL_GetTick() - last_baseline_report_ms) >= 10000U) {
        static char baseline[192];
        last_baseline_report_ms = HAL_GetTick();
        send_line("DIAG,FW,2026100501,2,2047\n");
        Chassis_FormatBaseline(baseline, sizeof(baseline));
        send_line(baseline);
        Motor_FormatCompensation(baseline, sizeof(baseline));
        send_line(baseline);
    }

    if ((uint32_t)(HAL_GetTick() - last_live_ms) >= 100U) {
        static char live[192];
        long current[TUNING_COUNT];
        uint32_t irq;
        unsigned int captured;
        last_live_ms = HAL_GetTick();
        do {
            irq=__get_PRIMASK();
            __disable_irq();
            tuning_refresh(current);
            __set_PRIMASK(irq);
            captured=Chassis_FormatLive(live,sizeof(live),tuning_revision);
        } while (captured!=tuning_known_gyro_revision);
        send_line(live);
    }

    /* Control diagnostics at 2 Hz, after replies; never print from the IMU ISR. */
    if ((uint32_t)(HAL_GetTick() - last_diagnostic_ms) >= 500U)
    {
        /* Poll has a single foreground caller. Avoid stacking these arrays
         * beneath snprintf and an interrupt on the small MCU stack. */
        static char motion[160], sensors[192], control[192], observations[192], calibration[192];
        last_diagnostic_ms = HAL_GetTick();
        tuning_reply(0,0);
        Chassis_FormatDiagnostics(motion, sizeof(motion), sensors, sizeof(sensors));
        send_line(motion);
        send_line(sensors);
        Chassis_FormatControlDiagnostics(control, sizeof(control), observations, sizeof(observations));
        send_line(control);
        send_line(observations);
        Chassis_FormatCalibration(calibration, sizeof(calibration));
        send_line(calibration);
        {
            char bluetooth[24];
            snprintf(bluetooth, sizeof(bluetooth), "DIAG,BT,%u\n", (unsigned int)Chassis_BluetoothEnabled());
            send_line(bluetooth);
        }
        if (turn_sequence && (uint32_t)(HAL_GetTick() - last_turn_diagnostic_ms) >= 1000U) {
            last_turn_diagnostic_ms = HAL_GetTick();
            Chassis_FormatTurnDiagnostics(turn_sequence,
                                          motion, sizeof(motion), sensors, sizeof(sensors));
            send_line(motion);
            send_line(sensors);
        }
    }

    /* STM32 owns battery sampling and publishes it without an ESP32 query. */
    if (Battery_IsReady(0) &&
        (uint32_t)(HAL_GetTick() - last_battery_report_ms) >= 1000U)
    {
        char report[48];
        long millivolts = (long)(battery * 1000.0f + 0.5f);
        last_battery_report_ms = HAL_GetTick();
        battery_sequence = (battery_sequence + 1U) & 0xffffU;
        snprintf(report, sizeof(report), "BAT,1,%u,%ld,%u,%u\n", battery_sequence,
                 millivolts, (unsigned int)Stop_Flag, (unsigned int)lower_power_flag);
        send_line(report);
    }
}
