#ifndef __PID_CONTROL_H
#define __PID_CONTROL_H

#include "ALLHeader.h"

extern volatile float Balance_Kp, Balance_Kd;
extern volatile float Velocity_Kp, Velocity_Ki;
extern float Turn_Kp, Turn_Kd;
extern volatile float myTurn_Kd;
void PID_ResetVelocity(void);
void PID_GetVelocityState(float *filtered, float *integral);
int Balance_PD(float Angle,float Gyro);
int Velocity_PI(int encoder_left,int encoder_right);
int Turn_PD(float gyro);
int myabs(int a);

#endif

