#include "bsp.h"
#include "intsever.h"
#include "ChassisLink/chassis_link.h"

void bsp_init(void)
{
    HAL_NVIC_DisableIRQ(EXTI15_10_IRQn);

    delay_init();
    init_led_gpio();
    init_beep();

    Motor_start();
    Encoder_Init_TIM3();
    Encoder_Init_TIM4();

    HAL_Delay(300);
    MPU6050_initialize();
    DMP_Init();
    OLED_I2C_Init();
    Battery_init();
}

void bsp_services_init(void)
{
    bluetooth_init();
    ChassisLink_Init();
    TIM6_Init();
}
