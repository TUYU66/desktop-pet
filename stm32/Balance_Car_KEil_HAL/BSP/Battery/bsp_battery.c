#include "bsp_battery.h"


//电池电量检测初始化
//Initialization of battery level detection
void Battery_init(void)
{
	 //HAL库不会自动配置部分  The HAL library does not automatically configure some
	  HAL_ADCEx_Calibration_Start(&hadc1);//启动ADC1校准，不校准将导致ADC测量不准确 Initiate ADC1 calibration, failure to calibrate will result in inaccurate ADC measurements
}




// 获得测得原始电压值
//Obtain the measured original voltage value
float Get_Measure_Volotage(void)
{
	uint16_t adcx;
	float temp;
	HAL_ADC_Start(&hadc1);
	HAL_ADC_PollForConversion(&hadc1,50);                 //轮询转换 Polling Conversion
	adcx = (uint16_t)HAL_ADC_GetValue(&hadc1);
	temp = (float)adcx * (3.30f / 4096);
	return temp;
}


// 获得实际电池分压前电压
//Obtain the actual voltage of the battery before voltage division
float Get_Battery_Volotage(void)
{
	float temp;
	temp = Get_Measure_Volotage();
	// 实际测量的值比计算得出的值低一点点。 The actual measured value is slightly lower than the calculated value.
	temp = temp * 4.03f;    //temp*(10+3.3)/3.3; 
	return temp;
}
