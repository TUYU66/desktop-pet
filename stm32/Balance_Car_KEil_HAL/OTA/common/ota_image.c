/*
* 镜像检查的具体实现。先做长度与向量范围检查，再遍历镜像计算CRC，避免用坏长度越界读取。
 */
#include "ota_image.h"

/* 按小端读取向量表字段，避免假设输入指针对齐。 */
static uint32_t read_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* IEEE反射式CRC32：初值全1，逐字节异或，逐位按0xEDB88320推进，末尾再次异或全1。 */
uint32_t OtaImage_Crc32(const uint8_t *bytes, size_t size)
{
    uint32_t crc = 0xFFFFFFFFU;
    size_t i;
    unsigned bit;
    for (i = 0; i < size; ++i) {
        crc ^= bytes[i];
        for (bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((crc & 1U) ? 0xEDB88320U : 0U);
    }
    return crc ^ 0xFFFFFFFFU;
}

/* 向量表前8字节分别是初始MSP和Reset_Handler。镜像即使暂存在Stage，其链接地址仍必须是App。 */
OtaImageResult OtaImage_Validate(const uint8_t *image, size_t size,
                                 uint32_t expected_crc)
{
    uint32_t stack, entry;
    if (image == NULL) return OTA_IMAGE_BAD_ARGUMENT;
    if (size < 8U || size > OTA_APP_SIZE || (size & 1U))
        return OTA_IMAGE_BAD_SIZE;
/* 初始栈必须在SRAM内且8字节对齐；RAM顶地址允许用作空栈顶，等于RAM底不允许。 */
    stack = read_le32(image);
    entry = read_le32(image + 4);
    /* Top-of-RAM is a legal initial descending stack pointer. */
    if (stack <= OTA_RAM_BASE || stack > OTA_RAM_END || (stack & 7U))
        return OTA_IMAGE_BAD_STACK;
/* Cortex-M只能执行Thumb，入口最低位必须为1。比较实际指令地址时用 &~1 清掉标志位，
 * 并检查其落在该镜像的App装载范围，不能指向Bootloader、Stage或镜像末尾之外。 */
    if (!(entry & 1U) || (entry & ~1U) < OTA_APP_BASE + 8U ||
        (entry & ~1U) >= OTA_APP_BASE + (uint32_t)size)
        return OTA_IMAGE_BAD_ENTRY;
    if (OtaImage_Crc32(image, size) != expected_crc)
        return OTA_IMAGE_BAD_CRC;
    return OTA_IMAGE_OK;
}
