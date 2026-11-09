/*
* 固件发布描述符：让打包器确认这个App真正编入了OTA入口，而不是只改了链接起始地址。
 * STM32的32位目标布局为36字节：12字节magic、4个uint32字段、4字节函数指针、4字节发布版本。
 * Python和Java按这些固定偏移验证；若修改字段顺序/长度，必须同步验证器。
 * 它是工程一致性检查，不是数字签名，也不是Bootloader向量表。
 */
#ifndef PET_OTA_RELEASE_H
#define PET_OTA_RELEASE_H
#include <stdint.h>
/* 11字符标识加结尾零，正好占magic[12]；整个镜像中要求只出现一份。 */
#define OTA_RELEASE_MAGIC "PETOTAAPPV1"
#define OTA_RELEASE_FORMAT 2U
#include "ota_version.h"
/* format：描述符格式；load_address/slot_size：App分区；capabilities含8192升级能力位。
 * entry：真实OtaEntry_Request函数指针，链接器会解析地址，目标地址必须位于App并带Thumb标志。 */
typedef struct {
    char magic[12];
    uint32_t format, load_address, slot_size, capabilities;
    int (*entry)(void);
    uint32_t firmware_version;
} OtaApplicationRelease;
#endif
