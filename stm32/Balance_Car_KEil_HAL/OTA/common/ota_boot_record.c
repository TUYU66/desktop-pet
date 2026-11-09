/*
* 读取首次整包的出厂记录。格式2双页日志的选择和恢复逻辑在 ota_update.c。
 * 正确记录 + 正确镜像才允许作为当前已安装版本，不只检查一个固定魔数。
 */
#include "ota_boot_record.h"

/* 从字节指针读取一个小端32位字段，不要求记录指针按4字节对齐。 */
static uint32_t word(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* 顺序：空指针→魔数/格式/版本/保留字段/提交标记/记录CRC→长度→实际镜像校验。
 * 这个顺序是安全边界：损坏的记录不能诱导程序扫描超出App分区的Flash。 */
OtaBootResult OtaBoot_Validate(const uint8_t *record, const uint8_t *image)
{
    uint32_t length;
    if (record == NULL || image == NULL) return OTA_BOOT_BAD_RECORD;
    if (word(record) != OTA_BOOT_RECORD_MAGIC ||
        word(record + 4) != OTA_BOOT_RECORD_FORMAT ||
        word(record + 8) == 0U || word(record + 20) != 0U ||
        word(record + 28) != OTA_BOOT_RECORD_COMMIT ||
        word(record + 24) != OtaImage_Crc32(record, 24U))
        return OTA_BOOT_BAD_RECORD;
    length = word(record + 12);
    if (length < 8U || length > OTA_APP_SIZE || (length & 1U))
        return OTA_BOOT_BAD_RECORD;
    return OtaImage_Validate(image, length, word(record + 16)) == OTA_IMAGE_OK
           ? OTA_BOOT_OK : OTA_BOOT_BAD_IMAGE;
}
