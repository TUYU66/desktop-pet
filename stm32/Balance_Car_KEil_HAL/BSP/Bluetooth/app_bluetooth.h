#ifndef __APP_BLUETOOTH_H_
#define __APP_BLUETOOTH_H_


#include "AllHeader.h"

void Bluetooth_DiscardInput(void);
int Bluetooth_TakePacket(char *out, unsigned int capacity);
void Bluetooth_ExecutePacket(const char *text);
void Init_PID(void);
void ResetPID(void);
void ProtocolGetPID(void);

void deal_bluetooth(uint8_t rxbuf);
void Protocol(void);

void SendAutoUp(void);//自动上报 放在time6里面 Automatically report and put it in time6
#endif

