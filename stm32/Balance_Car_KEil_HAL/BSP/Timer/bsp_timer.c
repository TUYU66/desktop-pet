#include "bsp_timer.h"

static float battery_All;
static uint8_t battery_count=0,battery_flag=0;
#define BATTERY_AVERAGE_SAMPLES 25U

u16 led_flag = 0;
u16 led_twinkle_count = 0;
u16 led_count = 0;
volatile u8 lower_power_flag = 0;
volatile u8 battery_sample_valid = 0;
volatile uint32_t battery_last_update_ms = 0;

u8 Battery_IsReady(uint32_t *age_ms)
{
    /* MonitorTask publishes under PRIMASK; aligned word reads are atomic. */
    u8 valid = battery_sample_valid;
    uint32_t updated = battery_last_update_ms;
    uint32_t age = (uint32_t)(HAL_GetTick() - updated);
    if (age_ms) *age_ms = age;
    return valid && age <= 2500U;
}

void BSP_TimerPeriodElapsed(TIM_HandleTypeDef *htim)
{
    (void)htim; /* No ADC polling or business work in timer interrupts. */
}

void Monitor_Poll10ms(void)
{
    static uint32_t previous;
    uint32_t now = HAL_GetTick();
    uint32_t elapsed = now - previous;
    float measured;
    if (elapsed < 10U) return;
    previous = now;
    /* Do not burst ADC samples when vTaskDelayUntil catches up after a stall. */
    if (elapsed > 2500U) {
        battery_sample_valid = 0;
        battery_All = 0;
        battery_count = battery_flag = 0;
    }
    led_count++;
    if (++battery_flag >= 3U) {
        battery_flag = 0;
        if (!Battery_TryRead(&measured)) {
            battery_sample_valid = 0;
            battery_All = 0;
            battery_count = 0;
        } else {
            battery_All += measured;
            if (++battery_count == BATTERY_AVERAGE_SAMPLES) {
                uint32_t mask = __get_PRIMASK();
                __disable_irq();
                battery = battery_All / BATTERY_AVERAGE_SAMPLES;
                battery_last_update_ms = HAL_GetTick();
                battery_sample_valid = 1;
                power_decect();
                __set_PRIMASK(mask);
                battery_All = 0;
                battery_count = 0;
            }
        }
    }
    cotrol_led();
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

