/*
* OTA 载荷协议：把经过外层 CRC16 验证的字符串转换为状态机调用。
 * 请求载荷：OTA,1,会话ID,递增序号,命令,...；本层不处理 @、*CRC16 和换行。
 * 正常流程：HELLO → BEGIN → DATA(多包) → END → INSTALL → RESET。
 * 写法：先校验并拆字段，再用命令分支调用状态机；这里不直接操作Flash寄存器。
 * 这样把“通信是否合法”和“现在是否能安装”分开，协议错误不应直接变成一次擦写。
 * ABORT 只请求状态机安全退出，不能绕过应用完整性检查。
 * 同会话、同序号、同载荷返回缓存回复；相同序号却改内容则丢弃，避免重复执行。
 */
#include "ota_protocol.h"
#include <stdio.h>
#include <string.h>

/* 严格读取十进制无符号32位整数：拒绝空串、负号、杂字符和溢出，不使用宽松的 atoi。 */
static int number(const char *s,uint32_t *n)
{
    uint32_t result=0,digit;
    if(!*s) return 0;
    for(;*s;++s) {
        if(*s<'0' || *s>'9') return 0;
        digit=(uint32_t)(*s-'0');
        if(result>(0xFFFFFFFFU-digit)/10U) return 0;
        result=result*10U+digit;
    }
    *n=result; return 1;
}
/* 单个十六进制字符转0～15；大小写均可，非法字符返回-1。 */
static int hex_digit(char c)
{
    if(c>='0' && c<='9') return c-'0';
    if(c>='A' && c<='F') return c-'A'+10;
    if(c>='a' && c<='f') return c-'a'+10;
    return -1;
}
/* 清会话缓存并绑定安装状态机；会话/序号只存 RAM，复位后重新握手。 */
void OtaProtocol_Init(OtaProtocol *p,OtaUpdate *u)
{
    memset(p,0,sizeof(*p)); p->update=u;
}
/* 返回的是内部 response 缓冲区，不需要调用者释放；下一条有效处理可能覆盖它。
 * 格式、会话或序号不合法时返回空指针，不执行 Flash 操作，也不会生成成功回复。 */
const char *OtaProtocol_Process(OtaProtocol *p,const char *payload)
{
    char *f[11],*cursor;
    unsigned count=0,i;
    uint32_t session,sequence,offset,size,address;
    OtaMetadata m;
    int ok=0,hi,lo;
    const char *error="COMMAND";
    size_t length=strlen(payload);
    if(length>=sizeof(p->work)) return 0;
/* 复制到工作区再按逗号原地切字段，避免破坏原始 payload；原始字符串还需用于重发一致性比较。 */
    memcpy(p->work,payload,length+1U); cursor=p->work;
    for(;;) {
        if(count>=11U) return 0;
        f[count++]=cursor;
        cursor=strchr(cursor,',');
        if(!cursor) break;
        *cursor++='\0';
    }
    if(count<5 || strcmp(f[0],"OTA") || strcmp(f[1],"1") ||
       !number(f[2],&session) || !session || !number(f[3],&sequence) || !sequence) return 0;
/* 最近一条回复的幂等缓存：成功和错误回复都可重放，不重新擦写。
 * 只缓存最近一条，不接受任意历史包重新执行。 */
    if(session==p->session && sequence==p->last_sequence) {
        if(!strcmp(payload,p->last_payload)) return p->response;
        return 0;
    }
/* 只有 HELLO 能建立不同的会话。关闭旧的 RAM 接收许可，随后必须 BEGIN 从头接收。
 * 它不清持久化候选/安装记录，安装中断电的恢复信息仍保留。 */
    if(!strcmp(f[4],"HELLO") && count==5 && session!=p->session) {
        p->session=session; p->last_sequence=0; p->update->receiving=0;
    }
    if(session!=p->session || sequence<=p->last_sequence) return 0;
    if(!strcmp(f[4],"HELLO") && count==5) ok=1;
/* BEGIN 字段为板型、版本、长度、CRC32十进制、App绝对地址；先限定板型和装载地址，再交给状态机。 */
    else if(!strcmp(f[4],"BEGIN") && count==10) {
        error="BEGIN";
        if(!strcmp(f[5],"YB-EST01-V2.0") && number(f[6],&m.version) &&
           number(f[7],&m.size) && number(f[8],&m.crc) && number(f[9],&address) && address==OTA_APP_BASE)
            ok=OtaUpdate_Begin(p->update,m);
/* DATA 带字节偏移和十六进制文本。512个字符最多表示256字节；字符数必须是4的倍数，
 * 使解码结果满足F1半字写入。不是把整段文本直接写进 Flash。 */
    } else if(!strcmp(f[4],"DATA") && count==7) {
        error="DATA"; size=(uint32_t)strlen(f[6]);
        if(number(f[5],&offset) && size && size<=512U && size%4U==0) {
            for(i=0;i<size;i+=2) {
                hi=hex_digit(f[6][i]); lo=hex_digit(f[6][i+1]);
                if(hi<0 || lo<0) break;
                p->data[i/2]=(uint8_t)(hi*16+lo);
            }
            if(i==size) ok=OtaUpdate_Data(p->update,offset,p->data,size/2U);
        }
    } else if(!strcmp(f[4],"END") && count==5) {
        error="IMAGE"; ok=OtaUpdate_End(p->update);
    } else if(!strcmp(f[4],"INSTALL") && count==5) {
        error="INSTALL"; ok=OtaUpdate_Install(p->update);
    } else if(!strcmp(f[4],"ABORT") && count==5) {
        error="ABORT"; ok=OtaUpdate_Abort(p->update);
/* RESET 必须已安装且 App 校验有效；这里只置标志，由主循环先发完确认再复位。 */
    } else if(!strcmp(f[4],"RESET") && count==5) {
        error="RESET"; ok=p->update->state==OTA_INSTALLED && OtaUpdate_CanBoot(p->update);
        if(ok) p->reset_requested=1;
    }
/* 合法会话内完成的命令会消耗序号并缓存回复，即使该命令被状态机拒绝。
 * 发送方重试同一操作要保持原始包；新操作使用更大的序号。 */
    p->last_sequence=sequence;
    memcpy(p->last_payload,payload,length+1U);
/* OK 返回：已收字节数、持久化状态、当前已安装版本；不等于网页已经确认应用启动。
 * ERR 返回错误阶段名称，供发送方区分明确拒绝与未收到匹配回复的超时。 */
    if(ok) snprintf(p->response,sizeof(p->response),"OTA,1,%lu,%lu,OK,%lu,%lu,%lu",
                    (unsigned long)session,(unsigned long)sequence,(unsigned long)p->update->received,
                    (unsigned long)p->update->state,(unsigned long)p->update->current.version);
    else snprintf(p->response,sizeof(p->response),"OTA,1,%lu,%lu,ERR,%s",
                  (unsigned long)session,(unsigned long)sequence,error);
    return p->response;
}
