/*
* 所有镜像共用的完整性/向量校验，既可校验App实际字节，也可校验Stage中的候选。
 * 只依赖标准整数和布局定义，便于与Python打包器交叉验证。
 */
#ifndef PET_OTA_IMAGE_H
#define PET_OTA_IMAGE_H
#include <stddef.h>
#include <stdint.h>
#include "ota_layout.h"

/* 返回第一个遇到的错误：参数、长度、初始栈、复位入口、CRC。0表示全部通过。 */
typedef enum {
    OTA_IMAGE_OK = 0,
    OTA_IMAGE_BAD_ARGUMENT,
    OTA_IMAGE_BAD_SIZE,
    OTA_IMAGE_BAD_STACK,
    OTA_IMAGE_BAD_ENTRY,
    OTA_IMAGE_BAD_CRC
} OtaImageResult;

/* IEEE CRC-32: reflected 0xEDB88320, init/final XOR 0xFFFFFFFF.
 * Matches Python zlib.crc32; NOT the default STM32 hardware CRC format. */
/* 软件CRC32与zlib一致；F1默认硬件CRC算法参数不同，不能直接替换。 */
uint32_t OtaImage_Crc32(const uint8_t *bytes, size_t size);
/* image may point to the staging slot. Vectors always target OTA_APP_BASE.
 * CRC is transport integrity only, not publisher authentication. */
/* 检查长度上限与偶数对齐、RAM初始栈、App范围内Thumb入口和整段CRC。
 * CRC不识别发布者身份，恶意更换镜像后重新计算CRC仍可通过；不要把它理解为数字签名。 */
OtaImageResult OtaImage_Validate(const uint8_t *image, size_t size,
                                 uint32_t expected_crc);
#endif
