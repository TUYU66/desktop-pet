/*
* 安装状态机的接口和状态字段。业务主程序不直接调用 Flash 复制逻辑。
 * Bootloader 通过 OtaPort_* 适配芯片；主机测试提供模拟 Flash 来验证相同状态机。
 * 除 CanBoot 的布尔判断外，各操作成功返回1，失败返回0；失败不等于所有写入都未发生，
 * 尤其安装阶段必须按持久化日志恢复，不能仅凭一次函数返回就跳转应用。
 */
#ifndef PET_OTA_UPDATE_H
#define PET_OTA_UPDATE_H
#include "ota_boot_record.h"

/* 格式2日志：64字节，小端字段序列，最后4字节为提交标记；详细偏移见 ota_update.c 的 decode()。 */
#define OTA_JOURNAL_MAGIC 0x324A5450U
#define OTA_JOURNAL_FORMAT 2U
#define OTA_JOURNAL_BYTES 64U
/* 状态含义：1已安装；2正在收Stage；3暂存镜像完整、等待安装；4可能已开始覆盖App。
 * 数字作为协议与Flash日志的一部分保存，不应随意调整或重新排序。 */
enum { OTA_INSTALLED=1, OTA_RECEIVING=2, OTA_READY=3, OTA_INSTALLING=4 };
/* 镜像元数据：version为递增包版本，size为包含偶数字节补齐后的长度，crc覆盖这段精确数据。 */
typedef struct { uint32_t version, size, crc; } OtaMetadata;
/* generation/state/current/candidate 会编码进日志；不是直接 memcpy 整个结构体。
 * current：已提交版本的元数据，安装中可能与半写App不匹配；candidate：待安装镜像元数据。
 * slot：最新有效日志页编号0/1，-1表示无有效槽；valid：是否拥有可信日志。
 * receiving：本次会话是否已完成Stage擦除并允许DATA；received：本轮顺序接收进度。
 * receiving/received 不持久化；接收中复位会丢失进度，需重新BEGIN，安装恢复不依赖它们。 */
typedef struct {
    uint32_t generation, state;
    OtaMetadata current, candidate;
    int slot, valid, receiving;
    uint32_t received;
} OtaUpdate;

/* Platform port. All addresses are Flash addresses, never untrusted pointers. */
const uint8_t *OtaPort_View(uint32_t address, uint32_t size);
int OtaPort_ErasePage(uint32_t address);
int OtaPort_Program(uint32_t address, const uint8_t *bytes, uint32_t size);

/* 读取双页日志及兼容出厂记录，仅恢复状态，不在这里自动安装或跳转。 */
void OtaUpdate_Init(OtaUpdate *u);
/* 对当前App做完整校验，并禁止在INSTALLING状态启动。 */
int OtaUpdate_CanBoot(const OtaUpdate *u);
/* 仅提交已完整存在于App的当前/候选镜像；没有旧版备份时不能凭空回滚。 */
int OtaUpdate_Abort(OtaUpdate *u);
/* 检查候选版本，提交接收状态并擦除Stage，不修改App。 */
int OtaUpdate_Begin(OtaUpdate *u, OtaMetadata candidate);
/* 顺序接收或确认相同内容重发，每包最多256字节，地址和长度均要求半字对齐。 */
int OtaUpdate_Data(OtaUpdate *u, uint32_t offset, const uint8_t *data, uint32_t size);
/* 验证整个Stage镜像后提交READY，仍未覆盖App。 */
int OtaUpdate_End(OtaUpdate *u);
/* 提交INSTALLING→按页覆盖App→整体验证→提交INSTALLED；复位后可从Stage重新复制。 */
int OtaUpdate_Install(OtaUpdate *u);
#endif
