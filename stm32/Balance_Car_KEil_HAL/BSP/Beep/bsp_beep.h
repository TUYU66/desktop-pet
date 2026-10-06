#ifndef __BSP_BEEP_H
#define __BSP_BEEP_H

#include "AllHeader.h"

#define BEEP_RCC   RCC_APB2Periph_GPIOA
#define BEEP_PORT  GPIOA
#define BEEP_PIN   GPIO_Pin_11


#define BEEP_ON   HAL_GPIO_WritePin(BEEP_GPIO_Port,BEEP_Pin,GPIO_PIN_SET);
#define BEEP_OFF  HAL_GPIO_WritePin(BEEP_GPIO_Port,BEEP_Pin,GPIO_PIN_RESET);

#define BEEP_BEEP PAout(11) 


void init_beep(void);
void beep_timer(void);



#endif
