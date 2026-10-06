#include <bsp_usart.h>

extern UART_HandleTypeDef huart1;

/* USER CODE BEGIN 0 */
//////////////////////////////////////////////////////////////////
//加入以下代码,支持printf函数,而不需要选择use MicroLIB	  //Add the following code to support the printf function without selecting use MicroLIB
#if 1
#pragma import(__use_no_semihosting)             
//标准库需要的支持函数   Support functions required by the standard library          
struct __FILE 
{ 
	int handle; 
}; 

FILE __stdout;       
//定义_sys_exit()以避免使用半主机模式   Define _sys_exit() to avoid using semihosting mode 
void _sys_exit(int x) 
{ 
	x = x; 
} 
//重定义fputc函数 Redefine fputc function
int fputc(int ch, FILE *f)
{      
		while((USART1->SR&0X40)==0);//Flag_Show!=0  使用串口1   Use serial port 1
		USART1->DR = (u8) ch;      
		return ch;
}

#endif
