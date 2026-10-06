#include "bsp_bluetooth.h"


void bluetooth_init(void)
{
	MX_UART5_Init();
	LL_USART_EnableIT_RXNE(UART5); // Start receiving interrupt 启动接收中断

	Init_PID();
	
}




// Send a Byte 发送一个字节
// data_byte:Sent data 发送的数据
void UART5_DataByte(uint8_t data_byte)
{
	while (!LL_USART_IsActiveFlag_TXE(UART5))
	{
	};
	LL_USART_TransmitData8(UART5, data_byte);
}

// Set to send a string 设置发送一个字符串
// data_str :The first address of the data 数据的首地址
// datasize :The length of data 数据的长度
void UART5_Send_Char(char *s)
{
	char *p;
	p=s;
	while(*p != '\0')
	{
		UART5_DataByte(*p);
		p++;
	}
}

//串口中断调用此文件  Serial port interrupt calls this file
void UART5_RX_deal(void)
{
	uint8_t rx5_temp;
	if (LL_USART_IsEnabledIT_RXNE(UART5)) // Determine if there is any interruption information 判断是否有中断信息
	{
		// LL_USART_ClearFlag_RXNE(UART5); //clear interrupt 清除中断
		rx5_temp = LL_USART_ReceiveData8(UART5); // Read information and clear interrupts 读取信息并清除中断
		deal_bluetooth(rx5_temp);					  // Processing data sent by bluetooth 处理蓝牙送来的数据
		//UART5_DataByte(rx5_temp);
	}
}

