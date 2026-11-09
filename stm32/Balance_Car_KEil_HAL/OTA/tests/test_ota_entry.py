import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EntryTests(unittest.TestCase):
    def test_reset_requires_seated_stopped_fresh_power_and_no_controller(self):
        compiler = os.environ.get('CC') or shutil.which('gcc')
        self.assertTrue(compiler)
        program = r'''
#include <assert.h>
#include <setjmp.h>
#include <stdint.h>
static int seated=1,ready=1,bt,turn,cal,gyro,irq;
static unsigned Stop_Flag=1;
static float battery=11.5f;
struct Timer {uint32_t CCR1,CCR2,CCR3,CCR4,BDTR;} timer;
struct Clock {uint32_t APB1ENR;} clock_port;
struct Power {uint32_t CR;} power_port;
struct Backup {uint32_t DR1,DR2;} backup;
#define TIM8 (&timer)
#define RCC (&clock_port)
#define PWR (&power_port)
#define BKP (&backup)
#define TIM_BDTR_MOE 0x8000U
#define RCC_APB1ENR_PWREN 1U
#define RCC_APB1ENR_BKPEN 2U
#define PWR_CR_DBP 4U
static jmp_buf reset;
static int Chassis_IsSeated(void){return seated;}
static int Battery_IsReady(void *age){(void)age;return ready;}
static int Chassis_BluetoothEnabled(void){return bt;}
static int Chassis_TurnBusy(void){return turn;}
static int Chassis_CalibrationActive(void){return cal;}
static int Chassis_GyroActive(void){return gyro;}
static void __disable_irq(void){irq=1;}
static void __DSB(void){}
static void NVIC_SystemReset(void){longjmp(reset,1);}
SOURCE
int main(void){
    int *flags[]={&seated,&ready,&bt,&turn,&cal,&gyro};unsigned i;
    for(i=0;i<6;++i){
        *flags[i]=i<2?0:1;
        assert(OtaEntry_Request()==0 && !irq && !backup.DR1);
        *flags[i]=i<2?1:0;
    }
    Stop_Flag=0;assert(!OtaEntry_Request());Stop_Flag=1;
    battery=10.7f;assert(!OtaEntry_Request());battery=11.5f;
    timer.CCR1=timer.CCR2=timer.CCR3=timer.CCR4=100;timer.BDTR=0xFFFF;
    if(!setjmp(reset)){OtaEntry_Request();assert(0);}
    assert(irq && !(timer.BDTR&TIM_BDTR_MOE));
    assert(!(timer.CCR1|timer.CCR2|timer.CCR3|timer.CCR4));
    assert(backup.DR1==0x4F54U && backup.DR2==0xB0ABU);
    return 0;
}
'''
        source = (ROOT / 'application/ota_entry.c').read_text(encoding='utf-8')
        source = '\n'.join(line for line in source.splitlines() if not line.startswith('#include'))
        with tempfile.TemporaryDirectory() as directory:
            source_path, binary = Path(directory)/'test.c', Path(directory)/'test.exe'
            source_path.write_text(program.replace('SOURCE', source), encoding='utf-8')
            subprocess.run([compiler, '-O2', '-std=c99', '-Wall', '-Wextra', '-Werror', str(source_path), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
