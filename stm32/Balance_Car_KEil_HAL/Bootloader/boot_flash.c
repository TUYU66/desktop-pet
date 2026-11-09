/*
* STM32F1 Flash 的底层适配层。上层状态机不直接操作 FLASH 寄存器。
 * 可写范围仅为应用、暂存区和日志；Bootloader 自身及尾部保留区禁止写入。
 * 擦除以 2KiB 页为单位，编程以 16bit 半字为单位；只能在停稳的升级阶段使用。
 * 上层状态机 → OtaPort_ErasePage/Program → 范围检查 → 解锁 → 硬件操作 → 等待/验证 → 上锁。
 * 把芯片细节集中在这一层，状态机就能在PC上换成模拟Flash测试断电恢复。
 */
#include "stm32f1xx.h"
#include "ota_update.h"

/* 把合法 Flash 地址范围转换为只读指针；先检查边界，再让上层读取。
 * 使用 size > end-address 的形式，避免 address+size 无符号溢出绕过检查。 */
const uint8_t *OtaPort_View(uint32_t address,uint32_t size)
{
    if(address<OTA_FLASH_BASE || address>OTA_FLASH_END || size>OTA_FLASH_END-address) return 0;
    return (const uint8_t *)address;
}
/* 验证可擦写范围 [OTA_APP_BASE, OTA_RESERVED_BASE)，右端不包含在内。
 * 它是底层最后一道范围保护；即使上层地址出错，也不能覆盖 Bootloader 或保留区。 */
static int writable(uint32_t address,uint32_t size)
{
    return address>=OTA_APP_BASE && address<OTA_RESERVED_BASE && size<=OTA_RESERVED_BASE-address;
}
/* 轮询 BSY 等待当前操作完成，并拒绝编程/写保护错误。
 * count 是循环次数上限，不是精确毫秒；当前没有 IWDG 硬件看门狗兜底。 */
static int ready(void)
{
    uint32_t count=8000000U;
    while(FLASH->SR&FLASH_SR_BSY) if(--count==0U) return 0;
    if(FLASH->SR&(FLASH_SR_PGERR|FLASH_SR_WRPRTERR)) return 0;
    return 1;
}
/* F1 要按顺序写入两个 KEYR 魔数才能解锁。SR 的这些标志通过写 1 清除，不能当普通变量赋零清除。 */
static void unlock(void)
{
    if(FLASH->CR&FLASH_CR_LOCK) { FLASH->KEYR=0x45670123U; FLASH->KEYR=0xCDEF89ABU; }
    FLASH->SR=FLASH_SR_EOP|FLASH_SR_PGERR|FLASH_SR_WRPRTERR;
}
/* 入口检查整页对齐和完整页范围。设置页擦除模式 PER、目标地址 AR，再用 STRT 启动。
 * 完成或失败后都撤销 PER 并重新上锁；Flash 擦除后通常为 0xFF，写入前应先擦除。 */
int OtaPort_ErasePage(uint32_t address)
{
    int ok;
    if(!writable(address,OTA_PAGE_SIZE) || address%OTA_PAGE_SIZE) return 0;
    unlock();
    if(!ready()) { FLASH->CR|=FLASH_CR_LOCK; return 0; }
    FLASH->CR|=FLASH_CR_PER; FLASH->AR=address; FLASH->CR|=FLASH_CR_STRT;
    ok=ready(); FLASH->CR&=~FLASH_CR_PER; FLASH->CR|=FLASH_CR_LOCK; return ok;
}
/* 要求地址、长度均为偶数。两个字节按小端拼成半字，写入后等待完成并立即读回比较。
 * 任何半字失败就停止；上层必须把失败作为未完成处理，不能靠这个函数自动重试安装。 */
int OtaPort_Program(uint32_t address,const uint8_t *bytes,uint32_t size)
{
    uint32_t offset;
    int ok=1;
    if(!bytes || !size || !writable(address,size) || (address&1U) || (size&1U)) return 0;
    unlock();
    if(!ready()) { FLASH->CR|=FLASH_CR_LOCK; return 0; }
    FLASH->CR|=FLASH_CR_PG;
    for(offset=0;offset<size;offset+=2) {
        uint16_t value=(uint16_t)(bytes[offset]|((uint16_t)bytes[offset+1]<<8));
        *(volatile uint16_t *)(address+offset)=value;
        if(!ready() || *(volatile uint16_t *)(address+offset)!=value) { ok=0; break; }
    }
    FLASH->CR&=~FLASH_CR_PG; FLASH->CR|=FLASH_CR_LOCK; return ok;
}
