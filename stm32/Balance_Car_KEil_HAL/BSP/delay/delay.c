#include "delay.h"

static uint32_t cycles_per_us;

void delay_init(void)
{
    /* SysTick belongs exclusively to HAL's 1 ms time base. I2C/OLED delays
     * must never change its clock, reload value, or enable state. */
    cycles_per_us = SystemCoreClock / 1000000U;
    if (cycles_per_us == 0U) cycles_per_us = 1U;
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    /* Do not reset CYCCNT: an interrupt may have an active delay. */
}

void delay_us(u32 nus)
{
    if (cycles_per_us == 0U) delay_init();
    while (nus != 0U)
    {
        /* Short chunks avoid multiplication overflow and handle counter wrap.
         * All timing variables are local, so interrupt callers cannot overwrite
         * an interrupted caller's deadline. Interrupts remain enabled. */
        uint32_t chunk = nus > 1000U ? 1000U : nus;
        uint32_t ticks = chunk * cycles_per_us;
        uint32_t start = DWT->CYCCNT;
        while ((uint32_t)(DWT->CYCCNT - start) < ticks) {}
        nus -= chunk;
    }
}

void delay_ms(u16 nms)
{
    while (nms != 0U)
    {
        delay_us(1000U);
        --nms;
    }
}
