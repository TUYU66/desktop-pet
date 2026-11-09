/*
* 主应用的SystemInit包装层：保留厂商时钟初始化，再把VTOR定位到偏移后的应用向量表。
 * Keil主工程编译本文件，不再另编译Core/Src/system_stm32f1xx.c，避免重复SystemInit定义。
 * 同时嵌入发布描述符，供打包脚本/Java确认固件保留下一次OTA的能力。
 */
/* Reuse the vendor clock support without modifying CubeMX-generated files.
 * This translation unit REPLACES system_stm32f1xx.c in the OTA target only. */
/* 预处理阶段临时改名厂商SystemInit，include后再撤销宏。
 * 这样能复用CubeMX生成的实现，同时给包装函数保留真正的SystemInit名字。 */
#define SystemInit OtaVendorSystemInit
#include "../../Core/Src/system_stm32f1xx.c"
#undef SystemInit
#include "../common/ota_release.h"
#include "../common/ota_layout.h"

extern int OtaEntry_Request(void);
/* 8192表示OTA能力位。函数指针引用真实入口，避免“描述符存在但入口根本没有链接”的假发布。 */
const OtaApplicationRelease OtaApplicationDescriptor = {
    OTA_RELEASE_MAGIC, OTA_RELEASE_FORMAT, OTA_APP_BASE, OTA_APP_SIZE, 8192U, OtaEntry_Request, PET_OTA_FIRMWARE_VERSION
};

/* 由应用启动汇编提供的向量表符号，链接器按应用起始地址放置。 */
extern const uint32_t __Vectors[];

/* 先执行厂商初始化，再按应用实际向量表设置VTOR；否则中断可能跑回Bootloader的向量表。 */
void SystemInit(void)
{
    OtaVendorSystemInit();
    /* Keep the descriptor and its real entry-point relocation in the linked
     * image. The packager rejects offset-only builds lacking this contract. */
/* 做一次volatile读取，让描述符及其函数指针重定位在链接裁剪时保留下来；并非调用升级入口。 */
    (void)((const volatile unsigned char *)&OtaApplicationDescriptor)[0];
    SCB->VTOR = (uint32_t)__Vectors;
    __DSB();
    __ISB();
}
