#ifndef __BSP_KEY_H__
#define __BSP_KEY_H__

#include "AllHeader.h"



// 按键状态，与实际电平相反。 The key status is opposite to the actual level.
#define KEY_PRESS      1
#define KEY_RELEASE    0

#define KEY_MODE_ONE_TIME   1
#define KEY_MODE_ALWAYS     0




uint8_t Key1_State(uint8_t mode);



#endif /* __BSP_KEY_H__ */
