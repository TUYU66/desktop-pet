#ifndef __BSP_BATTERY_H_
#define __BSP_BATTERY_H_

#include "AllHeader.h"


void Battery_init(void);
float Get_Measure_Volotage(void);
float Get_Battery_Volotage(void);

uint16_t Battery_Get_Average(uint8_t ch, uint8_t times);

#endif

