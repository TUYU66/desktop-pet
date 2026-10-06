#ifndef __APP_CONTROL_H_
#define __APP_CONTROL_H_

#include "ALLHeader.h" 

#define MPU6050_INT PAin(12)   //PA12连接到MPU6050的中断引脚  PA12 is connected to the interrupt pin of MPU6050

extern volatile u8 angle_sample_valid;
extern volatile uint32_t angle_last_update_ms;
/* 0 = normal balance, 1 = controlled rear lean, 2 = resting, 3 = fault stop. */
extern volatile u8 chassis_rest_state;
/* 0 = none, 1 = completed, 2 = canceled, 3 = IMU timeout. */
extern volatile u8 chassis_rest_event;
u8 Chassis_BeginRest(void);
void Chassis_CancelRest(void);
void Chassis_RestWatchdog(void);
/* Start: 0=accepted,1=not ready,2=battery,3=angle,4=unsafe posture,6=busy. */
u8 Chassis_StartBalance(void);
u8 Chassis_MotionWatchdog(void);
/* 1=left 90 degrees, 2=right 90 degrees, 3=right 180 degrees. */
u8 Chassis_BeginTurn(u8 direction);
u8 Chassis_TurnBusy(void);
u8 Chassis_TuningWritable(void);
u8 Chassis_GyroActive(void);
u8 Chassis_GyroCommand(u8 operation);
unsigned int Chassis_GyroRevision(void);
void Chassis_GyroWatchdog(void);
unsigned int Chassis_FormatGyro(char *text, unsigned int size, unsigned int revision);
unsigned int Chassis_FormatLive(char *text, unsigned int size, unsigned int revision);
/* Manual bench calibration; raw single-wheel PWM, normal mode remains default.
 * ARM requires stopped motors. SET expires after 1500 ms; continuous run <=8 s.
 * Exit/fault never automatically resumes balance. Errors: 1=not ready,2=battery,
 * 3=angle,4=must stop,5=busy,6=session,7=range,8=release required. */
u8 Chassis_CalibrationActive(void);
u8 Chassis_CalibrationKeyBlocked(void);
u8 Chassis_CalibrationCommand(u8 operation, unsigned int session, u8 wheel, int pwm, uint32_t sample_tick);
void Chassis_CalibrationWatchdog(void);
void Chassis_FormatCalibration(char *text, unsigned int size);
u8 Chassis_BluetoothEnabled(void);
u8 Chassis_IsSeated(void);
u8 Chassis_SetBluetooth(u8 enabled);
void Chassis_BluetoothMove(enCarState state);
void Chassis_BluetoothWatchdog(void);
void Chassis_TurnWatchdog(void);
u8 Chassis_TurnFailurePhase(void);
u8 Chassis_PostureResult(u8 standing);
void Chassis_FormatDiagnostics(char *motion, unsigned int motion_size,
                               char *sensors, unsigned int sensors_size);
void Chassis_FormatControlDiagnostics(char *text, unsigned int size,
                                      char *observations, unsigned int observations_size);
void Chassis_FormatBaseline(char *text, unsigned int size);
void Chassis_FormatTurnDiagnostics(unsigned int sequence, char *stable, unsigned int stable_size,
                                   char *timing, unsigned int timing_size);
extern volatile u8 chassis_turn_event; /* 1=done, 2=aborted */
extern volatile u8 chassis_turn_error; /* See TURN_ABORT reason mapping in chassis_link.c. */

/* Returns 0 when the raw sensor frame could not be read. */
u8 Get_Angle(u8 way);
int Pick_Up(float Acceleration,float Angle,int encoder_left,int encoder_right);
int Put_Down(float Angle,int encoder_left,int encoder_right);


#endif

