#ifndef __MOTOR_H
#define __MOTOR_H

#include "AllHeader.h"

#define L_PWMA   TIM8->CCR1  //PC6
#define L_PWMB   TIM8->CCR2  //PC7

#define R_PWMA   TIM8->CCR3  //PC8
#define R_PWMB   TIM8->CCR4  //PC9

void Motor_start(void);
#endif
