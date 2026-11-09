#ifndef __BSP_TIMER_H__
#define __BSP_TIMER_H__

#include "AllHeader.h"


extern volatile u8 battery_sample_valid;
extern volatile uint32_t battery_last_update_ms;
u8 Battery_IsReady(uint32_t *age_ms);

void Monitor_Poll10ms(void);
void BSP_TimerPeriodElapsed(TIM_HandleTypeDef *htim);

void power_decect(void);
void cotrol_led(void);

#endif
