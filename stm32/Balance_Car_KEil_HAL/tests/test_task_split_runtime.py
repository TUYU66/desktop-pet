"""Run production task boundaries against bounded queues and fake peripherals."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_freertos_runtime import function

ROOT = Path(__file__).resolve().parents[1]


class TaskSplitTests(unittest.TestCase):
    def compile_run(self, source, headers=None):
        with tempfile.TemporaryDirectory(prefix='stm32-split-') as directory:
            folder = Path(directory)
            for name, content in (headers or {}).items():
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('#pragma once\n' + content, encoding='latin1')
            file = folder / 'test.c'
            executable = folder / 'test.exe'
            file.write_text(source, encoding='latin1')
            result = subprocess.run([os.environ['CC'], '-std=c99', '-Wall', '-Wextra',
                '-I', str(folder), '-I', str(ROOT / 'Core/Inc'), '-I', str(ROOT / 'BSP'),
                str(file), '-o', str(executable)], capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_crc_receive_task_only_posts_copied_commands_and_recovers_overflow(self):
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "ChassisLink/link_frame.h"
#define CHASSIS_RX_CAPACITY 512
#define USART_SR_RXNE 32U
#define USART_SR_ORE 8U
#define USART_SR_FE 2U
#define USART_SR_NE 4U
#define USART_SR_PE 1U
static struct {uint32_t SR,DR;} uart;
#define USART2 (&uart)
static uint8_t rx_buffer[512], line_length, dropping_line=1, rx_overflow;
static uint16_t rx_head,rx_tail;
static uint32_t link_errors[8];
static char receive_line[128], posted[128];
static unsigned received;
static int in_irq;
uint32_t __get_PRIMASK(void) {return 0;}
void __disable_irq(void) {}
void __set_PRIMASK(uint32_t m) {(void)m;}
void AppRuntime_PostCommand(unsigned bluetooth,const char *text) {
    assert(!in_irq && !bluetooth); ++received; strcpy(posted,text);
}
'''
        source += function('BSP/ChassisLink/chassis_link.c', 'void ChassisLink_OnRxInterrupt(void)')
        source += function('BSP/ChassisLink/chassis_link.c', 'void ChassisLink_ReceivePoll(void)')
        source += r'''
static void byte(char ch) {
    uart.SR=USART_SR_RXNE; uart.DR=(uint8_t)ch;
    in_irq=1; ChassisLink_OnRxInterrupt(); in_irq=0;
}
static void input(const char *text) {while(*text) byte(*text++);}
static void packet(const char *text) {
    char frame[160];
    snprintf(frame,sizeof(frame),"@%s*%04X\n",text,link_crc16(text,strlen(text)));
    input(frame);
}
int main(void) {
    unsigned i;
    packet("STAND,1"); assert(received==0);
    ChassisLink_ReceivePoll(); assert(received==1 && !strcmp(posted,"STAND,1"));
    input("@STAND,2*0000\nSTAND,3\n"); ChassisLink_ReceivePoll();
    assert(received==1 && link_errors[0]==1);
    packet("BT,1,4,5"); packet("REST,6"); ChassisLink_ReceivePoll();
    assert(received==3 && !strcmp(posted,"REST,6"));
    for(i=0;i<600;++i) byte('x');
    assert(rx_overflow && link_errors[3]); ChassisLink_ReceivePoll();
    assert(received==3 && rx_tail==rx_head);
    packet("STAND,7"); ChassisLink_ReceivePoll();
    assert(received==4 && !strcmp(posted,"STAND,7"));
    uart.SR=USART_SR_ORE; ChassisLink_OnRxInterrupt();
    assert(rx_overflow && link_errors[2]==1);
    return 0;
}
'''
        self.compile_run(source)

    def test_command_fences_overload_safety_and_frame_priority(self):
        headers = {
            'FreeRTOS.h': '#include <stdint.h>\n#define pdPASS 1\n#define pdMS_TO_TICKS(x) (x)\n',
            'queue.h': 'typedef struct Queue *QueueHandle_t;\nQueueHandle_t xQueueCreate(unsigned,unsigned);\nint xQueueSend(QueueHandle_t,const void*,unsigned);\nint xQueueReceive(QueueHandle_t,void*,unsigned);\n',
            'task.h': 'void taskYIELD(void);\n',
            'AllHeader.h': '''#include <stdint.h>
typedef struct { int port; } USART_TypeDef;
extern USART_TypeDef esp_uart, ble_uart;
#define USART2 (&esp_uart)
#define UART5 (&ble_uart)
uint32_t HAL_GetTick(void);
uint32_t __get_PRIMASK(void);
void __disable_irq(void);
void __set_PRIMASK(uint32_t);
int LL_USART_IsActiveFlag_TXE(USART_TypeDef *);
void LL_USART_TransmitData8(USART_TypeDef *,uint8_t);
void Error_Handler(void);
void ChassisLink_ExecuteCommand(const char *);
void ChassisLink_RejectCommand(const char *,const char *);
void Bluetooth_ExecutePacket(const char *);
''',
        }
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "queue.h"
#include "AllHeader.h"
#include "app_runtime.h"
struct Queue { unsigned capacity, size, head, count; unsigned char *data; };
static uint32_t tick, mask;
static unsigned rejected, executed, revoked, sent_esp, sent_ble;
static int stuck;
static char last[128], esp_output[4096], ble_output[4096];
USART_TypeDef esp_uart = {0}, ble_uart = {1};
QueueHandle_t xQueueCreate(unsigned capacity, unsigned size) {
    QueueHandle_t q = calloc(1,sizeof(*q));
    q->capacity=capacity; q->size=size; q->data=calloc(capacity,size); return q;
}
int xQueueSend(QueueHandle_t q,const void *item,unsigned wait) {
    assert(!mask); (void)wait;
    if (q->count==q->capacity) return 0;
    memcpy(q->data + ((q->head+q->count)%q->capacity)*q->size,item,q->size);
    ++q->count; return 1;
}
int xQueueReceive(QueueHandle_t q,void *item,unsigned wait) {
    assert(!mask); (void)wait;
    if (!q->count) return 0;
    memcpy(item,q->data+q->head*q->size,q->size);
    q->head=(q->head+1)%q->capacity; --q->count; return 1;
}
uint32_t HAL_GetTick(void) { return tick; }
uint32_t __get_PRIMASK(void) {return mask;}
void __disable_irq(void) {mask=1;}
void __set_PRIMASK(uint32_t m) {mask=m;}
void taskYIELD(void) {++tick;}
int LL_USART_IsActiveFlag_TXE(USART_TypeDef *u) {(void)u; return !stuck;}
void LL_USART_TransmitData8(USART_TypeDef *u,uint8_t byte) {
    assert(!mask);
    if (u==USART2) esp_output[sent_esp++]=(char)byte;
    else ble_output[sent_ble++]=(char)byte;
}
void Error_Handler(void) {assert(0);}
void ChassisLink_ExecuteCommand(const char *text) {
    if (!strcmp(text,"BT_REVOKE")) {++revoked; AppRuntime_InvalidateBluetooth();}
    else {++executed; strcpy(last,text);}
}
void Bluetooth_ExecutePacket(const char *text) {++executed; strcpy(last,text);}
void ChassisLink_RejectCommand(const char *text,const char *why) {
    assert(text && (!strcmp(why,"BUSY") || !strcmp(why,"CANCELLED"))); ++rejected;
}
'''
        source += '#include "' + (ROOT / 'Core/Src/app_runtime.c').as_posix() + '"\n'
        source += r'''
static void drain_commands(void) {unsigned i; for(i=0;i<5;++i) AppRuntime_ActionPoll();}
static void drain_tx(void) {unsigned i; for(i=0;i<40;++i) AppRuntime_TransmitPoll();}
int main(void) {
    unsigned before,i;
    char mutable[32]="STAND,7";
    AppRuntime_Init(); assert(!AppRuntime_IsReady()); AppRuntime_SetReady();
    AppRuntime_PostCommand(0,mutable); strcpy(mutable,"REST,7");
    AppRuntime_ActionPoll(); assert(!strcmp(last,"STAND,7"));
    /* Safety OFF overtakes ordinary work and invalidates prior ON packets. */
    before=executed;
    AppRuntime_PostCommand(0,"BT,1,8");
    AppRuntime_PostCommand(0,"BT_REVOKE");
    drain_commands(); assert(executed==before && revoked==1 && rejected==1);
    AppRuntime_PostBluetooth("$1,0,0,0,0,0,0,0,0,0#",AppRuntime_BluetoothEpoch());
    AppRuntime_PostBluetooth("$0,0,0,0,0,0,0,0,0,0#",AppRuntime_BluetoothEpoch());
    AppRuntime_ActionPoll(); assert(executed==before+1 && !strcmp(last,"$0,0,0,0,0,0,0,0,0,0#"));
    /* A packet captured before an IRQ invalidation cannot acquire a new epoch. */
    before=executed; i=AppRuntime_BluetoothEpoch(); AppRuntime_InvalidateBluetooth();
    AppRuntime_PostBluetooth("$1,0,0,0,0,0,0,0,0,0#",i);
    drain_commands(); assert(executed==before);
    /* Normal commands expire; emergency OFF survives a scheduling stall. */
    tick=100; AppRuntime_PostCommand(0,"STAND,9"); tick=601;
    drain_commands(); assert(executed==before);
    AppRuntime_PostCommand(0,"BT_REVOKE"); tick+=1000;
    drain_commands(); assert(revoked==2);
    /* Unsigned age subtraction remains correct across HAL tick wrap. */
    tick=UINT32_MAX-100; AppRuntime_PostCommand(0,"STAND,10"); tick=100;
    drain_commands(); assert(!strcmp(last,"STAND,10"));
    before=executed;
    for(i=0;i<9;++i) AppRuntime_PostCommand(0,"BT,1,11");
    drain_commands(); assert(executed==before && revoked==3 && command_full==1);
    /* A full low queue cannot discard the next ACK. Producer memory is copied. */
    for(i=0;i<16;++i) assert(AppRuntime_Send(0,"DIAG,BT,0\n",1));
    assert(!AppRuntime_Send(0,"DIAG,BT,0\n",1));
    strcpy(mutable,"ACK,12\n"); assert(AppRuntime_Send(0,mutable,0));
    strcpy(mutable,"ERR,12\n"); AppRuntime_TransmitPoll();
    assert(!strncmp(esp_output,"@ACK,12*",8));
    esp_output[sent_esp]=0;
    {char packet[128]; strcpy(packet,esp_output+1); assert(link_decode(packet,strlen(packet)-1)==1); assert(!strcmp(packet,"ACK,12"));}
    drain_tx(); assert(reply_full==0 && telemetry_dropped==1);
    sent_ble=0; AppRuntime_Send(1,"$OK#",0); AppRuntime_TransmitPoll();
    assert(sent_ble==4 && !memcmp(ble_output,"$OK#",4));
    for(i=0;i<16;++i) assert(AppRuntime_Send(0,"ACK,13\n",0));
    assert(!AppRuntime_Send(0,"ACK,14\n",0));
    assert(reply_full==1); drain_commands(); assert(revoked==4); drain_tx();
    AppRuntime_Send(0,"ACK,15\n",0); stuck=1; before=tick;
    AppRuntime_TransmitPoll(); assert(tx_timeout==1 && (uint32_t)(tick-before)<=301);
    puts("queue, epoch, overload, CRC and TX priority regressions passed");
    return 0;
}
'''
        self.compile_run(source, headers)

    def test_monitor_validates_samples_and_never_samples_from_irq(self):
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#define __BSP_TIMER_H__
typedef uint8_t u8;
typedef uint16_t u16;
typedef struct {void *Instance;} TIM_HandleTypeDef;
static TIM_HandleTypeDef htim6;
static float battery;
static int LED, BEEP_BEEP, reads, healthy=1;
static uint32_t tick;
uint32_t HAL_GetTick(void) {return tick;}
uint32_t __get_PRIMASK(void) {return 0;}
void __disable_irq(void) {}
void __set_PRIMASK(uint32_t mask) {(void)mask;}
void HAL_TIM_Base_Start_IT(TIM_HandleTypeDef *t) {(void)t;}
int Battery_TryRead(float *out) {++reads; *out=11.4f; return healthy;}
void power_decect(void);
void cotrol_led(void);
'''
        source += (ROOT / 'BSP/Timer/bsp_timer.c').read_bytes().decode('latin1').replace('#include "bsp_timer.h"', '')
        source += r'''
int main(void) {
    unsigned i;
    for(i=0;i<100;++i) BSP_TimerPeriodElapsed(&htim6);
    assert(!reads && !battery_sample_valid);
    for(i=0;i<75;++i) {tick+=10; Monitor_Poll10ms();}
    assert(reads==25 && Battery_IsReady(0) && battery>11.39f && battery<11.41f);
    healthy=0;
    for(i=0;i<3;++i) {tick+=10; Monitor_Poll10ms();}
    assert(!Battery_IsReady(0));
    healthy=1;
    for(i=0;i<74;++i) {tick+=10; Monitor_Poll10ms();}
    assert(!Battery_IsReady(0));
    tick+=10; Monitor_Poll10ms(); assert(Battery_IsReady(0));
    tick+=2501; assert(!Battery_IsReady(0));
    puts("monitor sample validity, recovery and ISR isolation passed");
    return 0;
}
'''
        self.compile_run(source)


if __name__ == '__main__':
    unittest.main()
