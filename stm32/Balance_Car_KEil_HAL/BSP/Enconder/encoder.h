#ifndef __ENCODER_H
#define __ENCODER_H

#include "AllHeader.h" 


#define ENCODER_TIM_PERIOD (u16)(65535)   //不可大于65535 因为F103的定时器是16位的。  It cannot be greater than 65535 because the F103 timer is 16 bits.


int Read_Encoder(Motor_ID);
void Encoder_Init_TIM3(void);
void Encoder_Init_TIM4(void);


#endif
