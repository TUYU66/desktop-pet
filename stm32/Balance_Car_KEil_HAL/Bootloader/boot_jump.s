; Bootloader 跳转尾部：r0=应用初始 MSP，r1=应用 Reset_Handler（最低位必须为 1）。
; 外部中断/外设已由 boot_main.c 清理；切换栈后不再返回 C 函数或使用原 C 栈。
                PRESERVE8
                THUMB
                AREA |.text|, CODE, READONLY
                EXPORT Boot_Jump
; r0 = initial MSP, r1 = Thumb reset entry. All peripheral IRQs are off.
; Restore reset-like mask/control registers without returning to a C frame.
Boot_Jump       PROC
; r2 作为零值：恢复特权线程模式，并选择 MSP 主栈。
                MOVS r2, #0
                MSR CONTROL, r2
                ISB
; 清除优先级屏蔽和故障屏蔽，避免把 Bootloader 的屏蔽状态带入应用。
                MSR BASEPRI, r2
                MSR FAULTMASK, r2
                MSR PSP, r2
; 从此处开始使用应用栈。前面清 PSP，应用的 RTOS 之后会自己建立任务栈。
                MSR MSP, r0
; 恢复可屏蔽中断；外部 IRQ/SysTick 已清理，应用会在初始化时重新配置。
                CPSIE i
; BX 根据目标最低位进入 Thumb 指令流，直接执行应用复位入口而不是 main。
                BX r1
                ENDP
                END
