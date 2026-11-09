/*
* OTA 安装状态机：负责持久化日志、暂存接收、安装和恢复，不依赖具体串口或寄存器。
 * 主路径：INSTALLED → RECEIVING → READY → INSTALLING → INSTALLED。
 * 接收只写 Stage；确认完整后才覆盖 App。覆盖前先提交 INSTALLING，复位后据此重新复制。
 * 双页 Journal 交替提交；received/receiving 只在 RAM，接收中复位后必须从头重传。
 * 建议结合 ota_update.h 的字段说明及 save()/OtaUpdate_Install() 阅读。
 * 接口调用关系：Begin建立接收 → Data累积到Stage → End提交READY → Install覆盖App。
 * Begin/End/Install/Abort通过save提交状态；初始化根据这些状态恢复，不相信上次RAM进度。
 */
#include "ota_update.h"
#include <string.h>

/* 从字节流读取小端 uint32，不依赖 C 结构体布局、填充或地址对齐。 */
static uint32_t rd(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
/* 以小端序写入日志字段，使 Python 生成工具与 MCU 读取规则一致。 */
static void wr(uint8_t *p, uint32_t n)
{
    p[0]=(uint8_t)n; p[1]=(uint8_t)(n>>8); p[2]=(uint8_t)(n>>16); p[3]=(uint8_t)(n>>24);
}
/* 先检查版本非零、镜像至少含两个向量且不超过 App 分区，长度还必须满足半字写入。
 * 元数据合法不代表实际镜像完整，完整性还需 image_valid() 检查。 */
static int metadata_valid(OtaMetadata m)
{
    return m.version && m.size>=8U && m.size<=OTA_APP_SIZE && !(m.size&1U);
}
/* 日志中一个镜像元数据占 12 字节：版本、镜像长度、CRC32，各占 4 字节。 */
static OtaMetadata metadata(const uint8_t *p)
{
    OtaMetadata m; m.version=rd(p); m.size=rd(p+4); m.crc=rd(p+8); return m;
}
/* 校验指定分区的实际镜像。Stage 保存的也是按 App 地址链接的固件，入口应落在 App，而非 Stage。 */
static int image_valid(uint32_t address, OtaMetadata m)
{
    const uint8_t *p;
    if (!metadata_valid(m)) return 0;
    p=OtaPort_View(address,m.size);
    return p && OtaImage_Validate(p,m.size,m.crc)==OTA_IMAGE_OK;
}
/* 验证一页日志的完整提交记录，只有 CRC、提交标记和字段约束全通过才接受。
 * 64 字节布局：0魔数、4格式、8代次、12状态、16当前元数据、28候选元数据、
 * 40～55保留零、56前56字节的CRC、60提交标记；所有数值字段均为小端。 */
static int decode(const uint8_t *p, OtaUpdate *u, int slot)
{
    unsigned i;
    if (!p || rd(p)!=OTA_JOURNAL_MAGIC || rd(p+4)!=OTA_JOURNAL_FORMAT ||
        !rd(p+8) || rd(p+12)<OTA_INSTALLED || rd(p+12)>OTA_INSTALLING ||
        rd(p+56)!=OtaImage_Crc32(p,56U) || rd(p+60)!=OTA_BOOT_RECORD_COMMIT) return 0;
    for(i=40;i<56;i+=4) if(rd(p+i)) return 0;
    memset(u,0,sizeof(*u));
    u->generation=rd(p+8); u->state=rd(p+12);
    u->current=metadata(p+16); u->candidate=metadata(p+28);
    if (!metadata_valid(u->current) || (u->state!=OTA_INSTALLED && !metadata_valid(u->candidate))) return 0;
    if (u->state==OTA_INSTALLED && (u->candidate.version || u->candidate.size || u->candidate.crc)) return 0;
    u->slot=slot; u->valid=1; return 1;
}
/* 双页交替提交的核心：永远先擦写另一页，旧有效页暂时不动。
 * 先写前60字节，再最后写4字节提交标记，并读回解码验证，成功后才更新 RAM 状态。
 * 中途断电导致新页不完整时，重启仍可选择旧页；不直接把结构体强转成字节写 Flash。 */
static int save(OtaUpdate *u, OtaUpdate next)
{
    uint8_t bytes[OTA_JOURNAL_BYTES];
    uint32_t address;
    OtaUpdate check;
    int slot=u->slot==0?1:0;
/* 拒绝代次回绕；初始化用数值大小判断新旧，回绕会让旧日志被误当成新日志。 */
    if(u->generation==0xFFFFFFFFU) return 0;
    next.generation=u->generation+1U;
    memset(bytes,0,sizeof(bytes));
    wr(bytes,OTA_JOURNAL_MAGIC); wr(bytes+4,OTA_JOURNAL_FORMAT);
    wr(bytes+8,next.generation); wr(bytes+12,next.state);
    wr(bytes+16,next.current.version); wr(bytes+20,next.current.size); wr(bytes+24,next.current.crc);
    wr(bytes+28,next.candidate.version); wr(bytes+32,next.candidate.size); wr(bytes+36,next.candidate.crc);
    wr(bytes+56,OtaImage_Crc32(bytes,56)); wr(bytes+60,OTA_BOOT_RECORD_COMMIT);
    address=OTA_JOURNAL_BASE+(uint32_t)slot*OTA_PAGE_SIZE;
    /* Old page survives until this new record is fully committed and read back. */
    if(!OtaPort_ErasePage(address) || !OtaPort_Program(address,bytes,60) ||
       !OtaPort_Program(address+60,bytes+60,4) ||
       !decode(OtaPort_View(address,sizeof(bytes)),&check,slot)) return 0;
    next.slot=slot; next.valid=1; *u=next; return 1;
}

/* 启动恢复：分别验证两页，选择有效且代次较大的记录。
 * 如果两份格式2日志均无效，再尝试读取首次整包烧录的格式1出厂记录。
 * 格式1迁移只在 RAM 标记为已安装，首次后续 save 才写格式2；不在启动时无条件擦日志。 */
void OtaUpdate_Init(OtaUpdate *u)
{
    OtaUpdate a,b;
    int av,bv;
    const uint8_t *old;
    memset(u,0,sizeof(*u)); u->slot=-1;
    av=decode(OtaPort_View(OTA_JOURNAL_BASE,OTA_JOURNAL_BYTES),&a,0);
    bv=decode(OtaPort_View(OTA_JOURNAL_BASE+OTA_PAGE_SIZE,OTA_JOURNAL_BYTES),&b,1);
    if(av || bv) { *u=(!bv || (av && a.generation>=b.generation))?a:b; return; }
    /* Migration preserves the installed phase-1 factory record in page zero. */
    old=OtaPort_View(OTA_JOURNAL_BASE,OTA_BOOT_RECORD_SIZE);
    if(old && OtaBoot_Validate(old,OtaPort_View(OTA_APP_BASE,OTA_APP_SIZE))==OTA_BOOT_OK) {
        u->current.version=rd(old+8); u->current.size=rd(old+12); u->current.crc=rd(old+16);
        u->state=OTA_INSTALLED; u->slot=0; u->valid=1;
    }
}
/* 允许启动的必要条件：日志有效、不处于覆盖应用阶段、当前元数据与 App 实际字节匹配。
 * RECEIVING/READY 不会改 App，因此旧应用完整时仍可启动；INSTALLING 一律先恢复。 */
int OtaUpdate_CanBoot(const OtaUpdate *u)
{
    return u->valid && u->state!=OTA_INSTALLING && image_valid(OTA_APP_BASE,u->current);
}
/* 安全取消不是恢复被擦除的旧固件：本方案没有独立旧版备份。
 * 优先保留仍完整的当前 App；若完整候选已复制到 App、只差日志提交，也可把候选提交为当前。
 * 两者都不通过校验则拒绝，不能让半写的应用变成可启动状态。 */
int OtaUpdate_Abort(OtaUpdate *u)
{
    OtaUpdate next=*u;
    if(!u->valid) return 0;
    /* Never turn a partially overwritten image into a bootable application.
     * A complete candidate already copied to App may safely be committed too. */
    if(!image_valid(OTA_APP_BASE,u->current)) {
        if(!image_valid(OTA_APP_BASE,u->candidate)) return 0;
        next.current=u->candidate;
    }
    if(u->state==OTA_INSTALLED) return 1;
    memset(&next.candidate,0,sizeof(next.candidate));
    next.state=OTA_INSTALLED; next.receiving=0; next.received=0;
    return save(u,next);
}
/* 建立新接收：拒绝无效日志、非法镜像元数据和不高于当前版本的包。
 * 若 INSTALLING 的 Stage 仍有效，应先续装，不允许直接擦掉恢复来源；Stage 损坏才允许重新传包。
 * 先持久化 RECEIVING，再擦 Stage。擦除失败时 receiving 仍为0，后续 DATA 不会误写。 */
int OtaUpdate_Begin(OtaUpdate *u, OtaMetadata candidate)
{
    OtaUpdate next=*u;
    uint32_t address;
    if(!u->valid || !metadata_valid(candidate) ||
       candidate.version<=u->current.version) return 0;
    /* Preserve a usable staging image for automatic resume. If it is damaged,
     * permit a fresh upload, committing RECEIVING before erasing staging. */
    if(u->state==OTA_INSTALLING && image_valid(OTA_STAGE_BASE,u->candidate)) return 0;
    next.candidate=candidate; next.state=OTA_RECEIVING; next.received=0; next.receiving=0;
    if(!save(u,next)) return 0;
    for(address=OTA_STAGE_BASE;address<OTA_STAGE_BASE+OTA_STAGE_SIZE;address+=OTA_PAGE_SIZE)
        if(!OtaPort_ErasePage(address)) return 0;
    u->receiving=1; return 1;
}
/* 处理最多256字节的顺序包，offset 是相对于镜像起点的字节偏移，不是 Flash 绝对地址。
 * 只接受紧邻 received 的新数据；较早偏移仅在内容与已写字节完全一致时视为重复包。
 * 这样 ACK 丢失后的重发可以确认成功，但不同内容或未来偏移不能跳过未写区。 */
int OtaUpdate_Data(OtaUpdate *u, uint32_t offset, const uint8_t *data, uint32_t size)
{
    const uint8_t *previous;
    if(!u->receiving || u->state!=OTA_RECEIVING || !data || !size || size>256U ||
       (offset&1U) || (size&1U) || offset>u->candidate.size || size>u->candidate.size-offset) return 0;
    if(offset<u->received) {
        if(size>u->received-offset) return 0;
        previous=OtaPort_View(OTA_STAGE_BASE+offset,size);
        return previous && memcmp(previous,data,size)==0;
    }
/* 新包仅写暂存区，读回一致后才增加 received；此阶段绝不擦写应用区。 */
    if(offset!=u->received || !OtaPort_Program(OTA_STAGE_BASE+offset,data,size)) return 0;
    previous=OtaPort_View(OTA_STAGE_BASE+offset,size);
    if(!previous || memcmp(previous,data,size)!=0) return 0;
    u->received+=size; return 1;
}
/* END 只表示传输完成：长度收齐且整个暂存镜像通过向量/CRC校验后，提交 READY。
 * 它本身不安装，也不复位；发送方还必须显式发送 INSTALL。 */
int OtaUpdate_End(OtaUpdate *u)
{
    OtaUpdate next=*u;
    if(!u->receiving || u->received!=u->candidate.size || !image_valid(OTA_STAGE_BASE,u->candidate)) return 0;
    next.state=OTA_READY; next.receiving=0; return save(u,next);
}
/* 安装前重新验证 Stage。READY 必须先提交 INSTALLING，才能开始擦 App。
 * 若上次断电已留下 INSTALLING，本次从第0页重新复制，不依赖某个易丢失的复制偏移。
 * 整个复制期间 Stage 保持完整，最后 App 校验成功且 INSTALLED 提交后，才算安装确认。 */
int OtaUpdate_Install(OtaUpdate *u)
{
    uint32_t offset,amount;
    OtaUpdate next=*u;
    const uint8_t *source;
    if(!u->valid || (u->state!=OTA_READY && u->state!=OTA_INSTALLING) ||
       !image_valid(OTA_STAGE_BASE,u->candidate)) return 0;
    if(u->state==OTA_READY) {
        next.state=OTA_INSTALLING;
        if(!save(u,next)) return 0;
    }
    /* Recovery deliberately restarts copying from page zero. The complete
     * staging image remains untouched through the final journal commit. */
/* 擦除全部54个应用页，防止新固件较短时尾部残留旧程序。
 * 只给新镜像实际覆盖的页写数据；最后一页写真实长度，其余保持擦除态。 */
    for(offset=0;offset<OTA_APP_SIZE;offset+=OTA_PAGE_SIZE) {
        if(!OtaPort_ErasePage(OTA_APP_BASE+offset)) return 0;
        if(offset>=u->candidate.size) continue;
        amount=u->candidate.size-offset;
        if(amount>OTA_PAGE_SIZE) amount=OTA_PAGE_SIZE;
        source=OtaPort_View(OTA_STAGE_BASE+offset,amount);
        if(!source || !OtaPort_Program(OTA_APP_BASE+offset,source,amount)) return 0;
    }
    if(!image_valid(OTA_APP_BASE,u->candidate)) return 0;
/* 只有 App 完整校验成功才升级 current 版本并清除 candidate。
 * 如果最终日志提交断电，旧 INSTALLING 记录还在，下次仍可用完整 Stage 重装。 */
    next=*u; next.current=u->candidate; memset(&next.candidate,0,sizeof(next.candidate));
    next.state=OTA_INSTALLED; next.receiving=0; next.received=0;
    return save(u,next);
}
