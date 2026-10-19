#ifndef CHASSIS_LINK_H
#define CHASSIS_LINK_H

/* J13: USART2 PA2 TX, PA3 RX, 115200 8N1. Battery push and bounded motion commands. */
void ChassisLink_Init(void);
void ChassisLink_Poll(void);
void ChassisLink_OnRxInterrupt(void);
void ChassisLink_AppBluetoothRequest(unsigned int identifier, unsigned int enabled);
void ChassisLink_AppBluetoothStatus(char *buffer, unsigned int capacity);

#endif
