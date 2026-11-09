#ifndef CHASSIS_LINK_H
#define CHASSIS_LINK_H

/* J13: USART2 PA2 TX, PA3 RX, 115200 8N1. Battery push and bounded motion commands. */
void ChassisLink_Init(void);
void ChassisLink_Poll(void);
void ChassisLink_ReceivePoll(void);
void ChassisLink_ExecuteCommand(const char *text);
void ChassisLink_RejectCommand(const char *text, const char *reason);
void ChassisLink_OnRxInterrupt(void);
/* AppTask only, using the existing framed/CRC-protected UART sender. */
void ChassisLink_SendDiagnostic(const char *message);
void ChassisLink_AppBluetoothRequest(unsigned int identifier, unsigned int enabled);
void ChassisLink_AppBluetoothStatus(char *buffer, unsigned int capacity);

#endif
