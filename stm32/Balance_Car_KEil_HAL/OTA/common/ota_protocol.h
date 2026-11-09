/*
* 串口OTA命令处理器的RAM上下文，负责会话、严格序号和最近一次回复缓存。
 * 外层帧格式由 boot_main.c / link_frame.h 处理，安装持久化状态由 OtaUpdate 处理。
 */
#ifndef PET_OTA_PROTOCOL_H
#define PET_OTA_PROTOCOL_H
#include "ota_update.h"
/* 单帧缓冲上限，足以承载256字节数据的512字符十六进制文本及协议字段。 */
#define OTA_PROTOCOL_LINE 640U
/* update 指向安装状态机；session标识一次连接会话，last_sequence记录最近处理过的序号。
 * reset_requested 不直接复位，让调用方先发完整ACK。
 * last_payload 用来核对同序号重发内容；work 是可修改的分字段副本，response 为缓存回复。
 * data 是十六进制解码后的原始字节，不与 Flash 页面缓冲混用。 */
typedef struct {
    OtaUpdate *update;
    uint32_t session, last_sequence;
    int reset_requested;
    char last_payload[OTA_PROTOCOL_LINE], work[OTA_PROTOCOL_LINE], response[128];
    uint8_t data[256];
} OtaProtocol;
/* 初始化不操作硬件，只清会话并绑定update。 */
void OtaProtocol_Init(OtaProtocol *p,OtaUpdate *update);
/* Validated payload, WITHOUT @, CRC or newline. Returns a response payload. */
/* 输入是已经校验CRC的纯载荷；返回内部回复字符串或空指针。回复指针无需释放。 */
const char *OtaProtocol_Process(OtaProtocol *p,const char *payload);
#endif
