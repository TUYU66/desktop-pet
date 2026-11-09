/*
* 格式1出厂记录：首次SWD整包烧录时放在Journal第一页开头，为首次启动提供可信镜像元数据。
 * 当前双页日志仍兼容它；首次升级提交后转为 ota_update.c 的格式2日志。
 * 记录不是C结构体序列化，所有uint32字段明确采用小端字节序。
 */
#ifndef PET_OTA_BOOT_RECORD_H
#define PET_OTA_BOOT_RECORD_H
#include "ota_image.h"

/* Phase-1 factory record, stored at OTA_JOURNAL_BASE. Little endian, no C
 * struct serialization. Future installation journals must version this format. */
/* 32字节布局：0魔数、4格式、8版本、12长度、16镜像CRC、20保留零、
 * 24前24字节CRC、28提交标记。记录CRC保护元数据，镜像CRC保护实际固件。 */
#define OTA_BOOT_RECORD_SIZE    32U
#define OTA_BOOT_RECORD_MAGIC   0x31544F50U
#define OTA_BOOT_RECORD_FORMAT  1U
#define OTA_BOOT_RECORD_COMMIT  0x54494D43U

/* 区分记录自身不可信和实际App镜像不匹配，以便理解启动失败位置。 */
typedef enum {
    OTA_BOOT_OK = 0,
    OTA_BOOT_BAD_RECORD,
    OTA_BOOT_BAD_IMAGE
} OtaBootResult;

/* Validate metadata before trusting the image length or reading image bytes. */
/* 必须先验证记录内的长度，再按该长度读取镜像；不能拿未验证长度直接计算CRC。 */
OtaBootResult OtaBoot_Validate(const uint8_t *record, const uint8_t *image);
#endif
