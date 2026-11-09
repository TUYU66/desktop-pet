#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "ota_update.h"
#include "ota_protocol.h"

static uint8_t flash[0x40000],baseline[0x40000],old_image[1024],new_image[4098];
static unsigned operations,cut;
static uint32_t blocked_page;
static OtaProtocol protocol;
static void word(uint8_t *p,uint32_t n) { p[0]=n; p[1]=n>>8; p[2]=n>>16; p[3]=n>>24; }
const uint8_t *OtaPort_View(uint32_t a,uint32_t n) {
    assert(a>=OTA_FLASH_BASE && a<=OTA_FLASH_END && n<=OTA_FLASH_END-a);
    return flash+a-OTA_FLASH_BASE;
}
static int power(void) { return ++operations!=cut; }
int OtaPort_ErasePage(uint32_t a) {
    assert(a>=OTA_APP_BASE && a<OTA_RESERVED_BASE && a%OTA_PAGE_SIZE==0);
    if(a==blocked_page || !power()) return 0;
    memset(flash+a-OTA_FLASH_BASE,255,OTA_PAGE_SIZE); return 1;
}
int OtaPort_Program(uint32_t a,const uint8_t *p,uint32_t n) {
    uint32_t i;
    assert(a>=OTA_APP_BASE && a<OTA_RESERVED_BASE && n<=OTA_RESERVED_BASE-a && !(a&1) && !(n&1));
    for(i=0;i<n;i+=2) {
        uint8_t *dest=flash+a-OTA_FLASH_BASE+i;
        if(!power()) return 0;
        assert((dest[0]&p[i])==p[i] && (dest[1]&p[i+1])==p[i+1]);
        dest[0]=p[i]; dest[1]=p[i+1];
    }
    return 1;
}
static OtaMetadata candidate(void) {
    OtaMetadata m={2,sizeof(new_image),0};m.crc=OtaImage_Crc32(new_image,sizeof(new_image));return m;
}
static void factory(void) {
    uint8_t *record;
    memset(flash,255,sizeof(flash));
    memset(old_image,0x55,sizeof(old_image));word(old_image,OTA_RAM_END);word(old_image+4,OTA_APP_BASE+9);
    memset(new_image,0xAA,sizeof(new_image));word(new_image,OTA_RAM_END);word(new_image+4,OTA_APP_BASE+9);
    memcpy(flash+OTA_APP_BASE-OTA_FLASH_BASE,old_image,sizeof(old_image));
    record=flash+OTA_JOURNAL_BASE-OTA_FLASH_BASE;
    word(record,OTA_BOOT_RECORD_MAGIC);word(record+4,1);word(record+8,1);
    word(record+12,sizeof(old_image));word(record+16,OtaImage_Crc32(old_image,sizeof(old_image)));
    word(record+20,0);word(record+24,OtaImage_Crc32(record,24));word(record+28,OTA_BOOT_RECORD_COMMIT);
    operations=cut=0;blocked_page=0;
}
static void receive(OtaUpdate *u) {
    unsigned offset,n;
    assert(OtaUpdate_Begin(u,candidate()));
    for(offset=0;offset<sizeof(new_image);offset+=n) {
        n=sizeof(new_image)-offset;if(n>256)n=256;
        assert(OtaUpdate_Data(u,offset,new_image+offset,n));
    }
    assert(OtaUpdate_End(u));
}
static void journal_and_install_power_cuts(void) {
    OtaUpdate u,reboot;
    unsigned total,point;
    factory(); OtaUpdate_Init(&u); assert(OtaUpdate_CanBoot(&u));
    operations=0;assert(OtaUpdate_Begin(&u,candidate()));total=operations;
    for(point=1;point<=total;++point) {
        factory();OtaUpdate_Init(&u);operations=0;cut=point;
        assert(!OtaUpdate_Begin(&u,candidate()));
        cut=0;OtaUpdate_Init(&reboot);assert(OtaUpdate_CanBoot(&reboot));
        assert(!memcmp(flash+OTA_APP_BASE-OTA_FLASH_BASE,old_image,sizeof(old_image)));
    }
    factory();OtaUpdate_Init(&u);receive(&u);memcpy(baseline,flash,sizeof(flash));
    operations=0;assert(OtaUpdate_Install(&u));total=operations;
    for(point=1;point<=total;++point) {
        memcpy(flash,baseline,sizeof(flash));OtaUpdate_Init(&u);
        operations=0;cut=point;assert(!OtaUpdate_Install(&u));
        cut=0;OtaUpdate_Init(&reboot);
        if(reboot.state==OTA_INSTALLING) {
            assert(!OtaUpdate_CanBoot(&reboot));
            assert(OtaUpdate_Install(&reboot));
        } else {
            assert(reboot.state==OTA_READY && OtaUpdate_CanBoot(&reboot));
            assert(OtaUpdate_Install(&reboot));
        }
        OtaUpdate_Init(&u);assert(u.state==OTA_INSTALLED && u.current.version==2 && OtaUpdate_CanBoot(&u));
        assert(!memcmp(flash+OTA_APP_BASE-OTA_FLASH_BASE,new_image,sizeof(new_image)));
        assert(!memcmp(flash+OTA_STAGE_BASE-OTA_FLASH_BASE,new_image,sizeof(new_image)));
        assert(flash[0]==255 && flash[OTA_RESERVED_BASE-OTA_FLASH_BASE]==255);
    }
    printf("Recovered all %u installation erase/halfword-write cut points\n",total);
}
static void rejected_and_duplicate_data(void) {
    OtaUpdate u;
    unsigned before;
    OtaMetadata m;
    factory();OtaUpdate_Init(&u);m=candidate();m.version=1;
    assert(!OtaUpdate_Begin(&u,m));m=candidate();m.size=OTA_APP_SIZE+2;
    assert(!OtaUpdate_Begin(&u,m));assert(operations==0);
    assert(OtaUpdate_Begin(&u,candidate()));
    assert(!OtaUpdate_Data(&u,2,new_image,256));
    assert(OtaUpdate_Data(&u,0,new_image,256));before=operations;
    assert(OtaUpdate_Data(&u,0,new_image,256) && before==operations);
    assert(!OtaUpdate_Data(&u,0,old_image,256));
    assert(!OtaUpdate_End(&u));assert(OtaUpdate_CanBoot(&u));
    OtaUpdate_Init(&u);assert(!OtaUpdate_Data(&u,0,new_image,256));
    receive(&u);flash[OTA_STAGE_BASE-OTA_FLASH_BASE+100]^=1;
    before=operations;assert(!OtaUpdate_Install(&u));assert(operations==before);
    assert(OtaUpdate_CanBoot(&u));
}
static void recovery_paths(void) {
    OtaUpdate u,reboot;
    unsigned total,point;
    factory();OtaUpdate_Init(&u);receive(&u);
    blocked_page=OTA_APP_BASE;
    assert(!OtaUpdate_Install(&u) && u.state==OTA_INSTALLING);
    assert(!OtaUpdate_Begin(&u,candidate()));
    blocked_page=0;memcpy(baseline,flash,sizeof(flash));
    operations=0;assert(OtaUpdate_Abort(&u));total=operations;
    assert(u.current.version==1 && OtaUpdate_CanBoot(&u) && !u.candidate.version);
    for(point=1;point<=total;point++) {
        memcpy(flash,baseline,sizeof(flash));OtaUpdate_Init(&u);operations=0;cut=point;
        assert(!OtaUpdate_Abort(&u));cut=0;OtaUpdate_Init(&reboot);
        assert(reboot.state==OTA_INSTALLING && !OtaUpdate_CanBoot(&reboot));
        assert(OtaUpdate_Abort(&reboot) && OtaUpdate_CanBoot(&reboot));
    }
    factory();OtaUpdate_Init(&u);receive(&u);blocked_page=OTA_APP_BASE+OTA_PAGE_SIZE;
    assert(!OtaUpdate_Install(&u));blocked_page=0;
    assert(!OtaUpdate_Abort(&u) && !OtaUpdate_CanBoot(&u));
    assert(!OtaUpdate_Begin(&u,candidate()));
    flash[OTA_STAGE_BASE-OTA_FLASH_BASE+100]^=1;
    receive(&u);assert(OtaUpdate_Install(&u) && OtaUpdate_CanBoot(&u));
    factory();OtaUpdate_Init(&u);receive(&u);blocked_page=OTA_APP_BASE;
    assert(!OtaUpdate_Install(&u));blocked_page=0;
    memcpy(flash+OTA_APP_BASE-OTA_FLASH_BASE,new_image,sizeof(new_image));
    flash[OTA_STAGE_BASE-OTA_FLASH_BASE+100]^=1;
    assert(OtaUpdate_Abort(&u) && u.current.version==2 && OtaUpdate_CanBoot(&u));
    puts("Recovery: safe abort, every abort journal cut, partial App refusal, damaged staging reupload and candidate promotion passed");
}
static void protocol_guards(void) {
    OtaUpdate u;
    char payload[256],saved[128];
    const char *reply;
    unsigned before;
    factory();OtaUpdate_Init(&u);OtaProtocol_Init(&protocol,&u);
    assert(!OtaProtocol_Process(&protocol,"OTA,1,1,1,DATA,0,AAAA"));
    reply=OtaProtocol_Process(&protocol,"OTA,1,1,1,HELLO");assert(reply && strstr(reply,",OK,"));
    snprintf(payload,sizeof(payload),"OTA,1,1,2,BEGIN,YB-EST01-V2.0,2,%u,%lu,%lu",
             (unsigned)sizeof(new_image),(unsigned long)candidate().crc,(unsigned long)OTA_APP_BASE);
    reply=OtaProtocol_Process(&protocol,payload);assert(reply && strstr(reply,",OK,"));
    strcpy(saved,reply);before=operations;
    assert(!strcmp(saved,OtaProtocol_Process(&protocol,payload)) && before==operations);
    assert(!OtaProtocol_Process(&protocol,"OTA,1,1,2,DATA,0,AAAA"));
    reply=OtaProtocol_Process(&protocol,"OTA,1,1,3,DATA,0,00GG");assert(reply && strstr(reply,",ERR,"));
    assert(!OtaProtocol_Process(&protocol,"OTA,1,1,4294967296,HELLO"));
    assert(OtaProtocol_Process(&protocol,"OTA,1,2,1,HELLO"));
    reply=OtaProtocol_Process(&protocol,"OTA,1,2,2,DATA,0,AAAA");assert(reply && strstr(reply,",ERR,"));
    assert(!OtaProtocol_Process(&protocol,"OTA,1,1,4,END"));
    reply=OtaProtocol_Process(&protocol,"OTA,1,2,3,ABORT");assert(reply && strstr(reply,",OK,"));
    before=operations;strcpy(saved,reply);
    assert(!strcmp(saved,OtaProtocol_Process(&protocol,"OTA,1,2,3,ABORT")) && operations==before);
    assert(OtaUpdate_CanBoot(&u) && u.state==OTA_INSTALLED);
}
int main(int argc,char **argv) {
    (void)argv;
    if(argc>1) {
        static char line[640];
        const char *reply;
        OtaUpdate u;
        factory();OtaUpdate_Init(&u);OtaProtocol_Init(&protocol,&u);
        while(fgets(line,sizeof(line),stdin)) {
            line[strcspn(line,"\r\n")]='\0';
            reply=OtaProtocol_Process(&protocol,line);
            puts(reply?reply:"IGNORED");fflush(stdout);
            if(protocol.reset_requested) break;
        }
        return 0;
    }
    journal_and_install_power_cuts();rejected_and_duplicate_data();recovery_paths();protocol_guards();return 0;
}
