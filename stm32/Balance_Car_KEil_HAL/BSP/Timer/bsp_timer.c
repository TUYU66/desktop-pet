#include "bsp_timer.h"

static float battery_All;
static uint8_t battery_count=0,battery_flag=0;
#define BATTERY_AVERAGE_SAMPLES 25U

u16 led_flag = 0;
u16 led_twinkle_count = 0;
u16 led_count = 0;
u8 lower_power_flag = 0;
volatile u8 battery_sample_valid = 0;
volatile uint32_t battery_last_update_ms = 0;

u8 Battery_IsReady(uint32_t *age_ms)
{
    /* Sample the ISR-owned timestamp BEFORE the current time. Reading now
     * first can underflow if TIM6 publishes a newer timestamp in between. */
    u8 valid = battery_sample_valid;
    uint32_t updated = battery_last_update_ms;
    uint32_t age = (uint32_t)(HAL_GetTick() - updated);
    if (age_ms) *age_ms = age;
    return valid && age <= 2500U;
}

/**************************************************************************
Function function: TIM6 initialization, timed for 10 milliseconds
Entrance parameters: None
Return value: None
函数功能：TIM6初始化，定时10毫秒
入口参数：无
返回  值：无
**************************************************************************/
void TIM6_Init(void)
{
	// 打开定时器中断
	// Turn on timer interrupt
	HAL_TIM_Base_Start_IT(&htim6);
}


u8 bulettohflag = 0;

// TIM6中断
// 此回调函数可放多个定时器处理
// 传入参数：定时器结构体
// This callback function can handle multiple timers
// Incoming parameter: Timer structure
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
	if (htim->Instance == TIM6)
	{
		led_count++;  //led服务显示标志 LED service display logo
		battery_flag ++;		//电量显示标志	 Electricity display sign
		
        bulettohflag = 1;

////////电压检测流程		 Voltage detection process
		if(battery_flag > 2)// 30 ms at the configured 10 ms TIM6 period
		{		
			battery_flag = 0;
			battery_All += Get_Battery_Volotage();//获取电源电量 Obtain the power level of the power supply
			battery_count++;
			if(battery_count == BATTERY_AVERAGE_SAMPLES)// about 750 ms
			{
				battery = battery_All/BATTERY_AVERAGE_SAMPLES; //平均值 average value
				battery_All = 0; 
				battery_count = 0;
				battery_last_update_ms = HAL_GetTick();
				battery_sample_valid = 1;
				power_decect();//电压处理  Voltage processing
			}
			
		}
///////////
		
		cotrol_led();//灯服务  led service
		
				
		
	}
}


void power_decect(void)
{
	static u8 normal_power_flag = 1; //电压恢复标志 0：没恢复 1:恢复 //Voltage recovery flag 0: not restored 1: restored
	if(battery < 9.6) //小于9.6V报警 //Alarm below 9.6V
	{
		lower_power_flag = 1;
		normal_power_flag = 0;
	}
	else
	{
		if(normal_power_flag == 0)
		{
			lower_power_flag = 0;
			normal_power_flag = 1;
			BEEP_BEEP = 0;
		}
		
	}
}

void cotrol_led(void)
{
	//灯的效果和蜂鸣器的效果 低压报警 //The effect of the lamp and buzzer is low voltage alarm
		if(!led_flag)
		{
			if(led_count>300)//3S
			{
				led_count = 0;
				led_flag = 1;
			}
		}
		else
		{
			if(led_count>20)//200ms
			{
				led_count = 0;
				
				if(lower_power_flag == 0)
				{
					LED = !LED;//状态反转 //State reversal
				}
				else
				{
					BEEP_BEEP = !BEEP_BEEP;
					LED = 1;//低压蓝灯常亮 //Low voltage blue light is always on
				}
				
				led_twinkle_count++;
				if(led_twinkle_count == 6)
				{
					if(lower_power_flag == 0)
					{
						LED = 0;
					}
					else
					{
						BEEP_BEEP = 0;
					}
					
					led_twinkle_count = 0;
					led_flag = 0;
				}
				
			}
		}

}

