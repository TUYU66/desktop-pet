/*
* 本机 Flash/RAM 分区的数值来源。地址右边界不包含在区间中。
 * STM32F103RCT6：Flash 256KiB，SRAM 48KiB；Flash 擦除页为2KiB。
 * Flash：Bootloader 32KiB + App 108KiB + Stage 108KiB + Journal 4KiB + Reserved 4KiB。
 * App 是实际运行区；Stage 只存待安装镜像，不在该地址执行；Journal 是两页安装状态日志。
 * Python 打包器也直接读取本文件的数值定义。修改布局需要同步固件、服务端及首次整包安装，
 * 不能只修改一个地址就给现有设备传包。该布局现已用于主应用 OTA，并非尚未启用。
 */
#ifndef PET_OTA_LAYOUT_H
#define PET_OTA_LAYOUT_H

/* 仅适用于 STM32F103RCT6；所有结束地址均不包含在有效地址范围内。 */
/* 整个内部 Flash 的起始地址；BOOT_BASE 复用这个定义。 */
#define OTA_FLASH_BASE       0x08000000U
#define OTA_FLASH_END        0x08040000U
#define OTA_PAGE_SIZE        0x00000800U
#define OTA_BOOT_BASE        OTA_FLASH_BASE
#define OTA_BOOT_SIZE        0x00008000U
/* 主应用按此地址链接和放置向量表；打包后的 BIN 第0字节对应此地址。 */
#define OTA_APP_BASE         0x08008000U
#define OTA_APP_SIZE         0x0001B000U
/* 接收固件的暂存区，容量等于App。安装前收齐、校验，安装时作为复制源。 */
#define OTA_STAGE_BASE       0x08023000U
#define OTA_STAGE_SIZE       0x0001B000U
/* 两页日志，每页2KiB，但每份有效记录只用页首64字节。不能把剩余空间随意当普通数据区。 */
#define OTA_JOURNAL_BASE     0x0803E000U
#define OTA_JOURNAL_SIZE     0x00001000U
/* 尾部保留区；当前 Flash 适配层禁止擦写，暂不拿来保存额外旧镜像。 */
#define OTA_RESERVED_BASE    0x0803F000U
#define OTA_RESERVED_SIZE    0x00001000U
/* RAM_END 是排他边界，但它可以作为向下增长栈的初始栈顶（尚未压栈）。 */
#define OTA_RAM_BASE         0x20000000U
#define OTA_RAM_END          0x2000C000U

/* 编译期检查各分区无缝衔接，防止地址改动导致重叠或超出物理 Flash。 */
#if OTA_BOOT_BASE + OTA_BOOT_SIZE != OTA_APP_BASE || \
    OTA_APP_BASE + OTA_APP_SIZE != OTA_STAGE_BASE || \
    OTA_STAGE_BASE + OTA_STAGE_SIZE != OTA_JOURNAL_BASE || \
    OTA_JOURNAL_BASE + OTA_JOURNAL_SIZE != OTA_RESERVED_BASE || \
    OTA_RESERVED_BASE + OTA_RESERVED_SIZE != OTA_FLASH_END
#error OTA partitions must cover Flash without gaps or overlap
#endif
/* 再检查镜像槽大小相等、擦写页对齐，以及 Journal 恰好为两页。 */
#if OTA_APP_SIZE != OTA_STAGE_SIZE || OTA_APP_BASE % OTA_PAGE_SIZE || \
    OTA_APP_SIZE % OTA_PAGE_SIZE || OTA_STAGE_BASE % OTA_PAGE_SIZE || \
    OTA_BOOT_SIZE % OTA_PAGE_SIZE || OTA_JOURNAL_SIZE != 2 * OTA_PAGE_SIZE
#error OTA partitions must be page aligned with equal image slots
#endif
#endif
