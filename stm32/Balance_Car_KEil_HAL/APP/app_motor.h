#ifndef __APP_MOTOR_H_
#define __APP_MOTOR_H_

#include "ALLHeader.h" 

#include "motor_compensation_config.h"

/* Legacy rear-support lean only; normal balance uses measured wheel thresholds. */
#define MOTOR_IGNORE_PULSE 1300
#define MOTOR_BALANCE_PWM_LIMIT 2600
#define MOTOR_REST_PWM_LIMIT 1600

#define PI 3.14159265							//PI圆周率  PI π
#define Control_Frequency  200.0	//编码器读取频率  Encoder reading frequency
#define Diameter_67  67.0 				//轮子直径67mm   Wheel diameter 67mm
#define EncoderMultiples   4.0 		//编码器倍频数  Encoder multiples
#define Encoder_precision  11.0 	//编码器精度 11线  Encoder precision 11 lines
#define Reduction_Ratio  30.0			//减速比30  Reduction ratio 30
#define Perimeter  210.4867 			//周长，单位mm Perimeter, unit mm


extern uint8_t angle_max;

void Set_Pwm(int motor_left,int motor_right);
int PWM_Limit(int IN,int max,int min);

void Get_Velocity_Form_Encoder(int encoder_left,int encoder_right);
uint8_t Turn_Off(float angle, float voltage);
	
int PWM_Ignore(int pulse);
void Motor_ResetBalanceCompensation(void);
unsigned int Motor_GetAssistMask(void);
void Motor_FormatCompensation(char *text, unsigned int size);
void Motor_MixBalance(int common, int yaw, u8 commanded_turn,
                      int encoder_left, int encoder_right, int *left, int *right);

#endif

