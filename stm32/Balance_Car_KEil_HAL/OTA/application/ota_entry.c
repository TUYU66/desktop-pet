/*
* 正常应用收到升级命令后的安全入口。只负责“停稳并复位到Bootloader”，不接收或擦写固件。
 * 由现有动作所有者调用，不在中断回调中执行，也不创建另一个电机控制任务。
 */
#include "AllHeader.h"
#include "app_control.h"
#include "bsp_battery.h"

/* OTA-target only. Called by the action owner; never by an interrupt. */
/* 先检查坐下、停机、电池有效且至少10.8V，同时禁止蓝牙控制、转向、校准和陀螺仪操作。
 * 不满足条件返回0；满足条件通常会复位，不会以普通成功返回值结束。 */
int OtaEntry_Request(void)
{
    if(!Chassis_IsSeated() || !Stop_Flag || !Battery_IsReady(0) || battery<10.8f ||
       Chassis_BluetoothEnabled() || Chassis_TurnBusy() || Chassis_CalibrationActive() || Chassis_GyroActive()) return 0;
    /* A protected two-word backup marker survives the system reset. No Flash
     * writes in the running application, and no queue window for new motion. */
/* 通过安全检查后立即关中断，避免在清PWM和复位之间又被控制环写入运动输出。 */
    __disable_irq();
    Stop_Flag=1;
/* 清四路PWM比较值并关TIM8主输出MOE。该步骤不能替代前面的坐下/停稳检查。 */
    TIM8->CCR1=TIM8->CCR2=TIM8->CCR3=TIM8->CCR4=0;
    TIM8->BDTR&=~TIM_BDTR_MOE;
    RCC->APB1ENR|=RCC_APB1ENR_PWREN|RCC_APB1ENR_BKPEN;
    PWR->CR|=PWR_CR_DBP;
/* 使能备份域写入后写两个匹配魔数。系统复位后Bootloader消费它们，将握手窗口延长到30秒。
 * 这里不写Flash，也不修改应用镜像；标记是升级意图，不表示镜像已下载/安装。 */
    BKP->DR1=0x4F54U; BKP->DR2=0xB0ABU;
    __DSB(); NVIC_SystemReset();
    return 0;
}
