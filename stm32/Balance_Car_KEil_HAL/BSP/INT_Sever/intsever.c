/*
* @par Copyright (C): 2018-2028, Shenzhen Yahboom Tech
* @file         // intsever.c
* @author       // lly
* @version      // V1.0
* @date         // 20240628
* @brief
*/


#include "intsever.h"

void MPU6050_EXTI_Init(void)
{  
	HAL_NVIC_EnableIRQ(EXTI15_10_IRQn);//把mpu的外部中断打开 Turn on the external interrupt of the mpu
}






