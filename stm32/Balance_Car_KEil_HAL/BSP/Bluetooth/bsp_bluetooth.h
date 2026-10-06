#ifndef __BSP_BLUETOOTH_H_
#define __BSP_BLUETOOTH_H_

#include "ALLHeader.h"

void bluetooth_init(void);
void UART5_RX_deal(void);



void UART5_DataByte(uint8_t data_byte);
void UART5_Send_Char(char *s);


#endif
