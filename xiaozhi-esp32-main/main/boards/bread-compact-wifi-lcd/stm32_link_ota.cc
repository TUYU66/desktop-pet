#include "stm32_link.h"
#include "stm32_link_internal.h"
#include "link_frame.h"
#include "board.h"
#include "display.h"
#include "ota.h"
#include "settings.h"
#include <cJSON.h>
#include <esp_heap_caps.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <mbedtls/sha256.h>
#include <freertos/task.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <memory>
#include <stdexcept>
#include <vector>

using namespace stm32_link_detail;
struct Stm32Link::OtaWork { Stm32Link* self; std::string job,url,metadata; };
namespace {
std::string firmware_origin() {
    const auto url=Ota().GetCheckVersionUrl();
    const auto scheme=url.find("://");
    if(scheme==std::string::npos)return "";
    const auto origin=url.substr(0,url.find('/',scheme+3));
    return origin.find_first_of("@?# \r\n")==std::string::npos?origin:"";
}
uint32_t crc32(const uint8_t* p,size_t n) {
    uint32_t crc=0xFFFFFFFFU;
    for(size_t i=0;i<n;++i) {
        crc^=p[i];
        for(int b=0;b<8;++b) crc=(crc>>1)^((crc&1)?0xEDB88320U:0U);
    }
    return crc^0xFFFFFFFFU;
}
uint32_t integer(cJSON* json,const char* key,uint32_t maximum) {
    auto item=cJSON_GetObjectItemCaseSensitive(json,key);
    if(!cJSON_IsNumber(item) || !std::isfinite(item->valuedouble) || item->valuedouble<0 ||
       item->valuedouble>maximum || floor(item->valuedouble)!=item->valuedouble) throw std::runtime_error("固件元数据无效");
    return static_cast<uint32_t>(item->valuedouble);
}
std::string text(cJSON* json,const char* key) {
    auto item=cJSON_GetObjectItemCaseSensitive(json,key);
    return cJSON_IsString(item)?item->valuestring:"";
}
std::string dump(cJSON* json) {
    char* s=cJSON_PrintUnformatted(json); std::string result=s?s:"{}"; cJSON_free(s); cJSON_Delete(json); return result;
}
std::vector<std::string> split(const std::string& value) {
    std::vector<std::string> fields; size_t at=0,end;
    while((end=value.find(',',at))!=std::string::npos) {fields.push_back(value.substr(at,end-at));at=end+1;}
    fields.push_back(value.substr(at));return fields;
}
uint32_t number(const std::string& s) {
    uint64_t n=0;
    if(s.empty() || s.size()>10) throw std::runtime_error("升级响应格式错误");
    for(char c:s) {if(c<'0'||c>'9')throw std::runtime_error("升级响应格式错误");n=n*10+c-'0';}
    if(n>UINT32_MAX)throw std::runtime_error("升级响应数值越界");
    return n;
}
}
std::string Stm32Link::GetOtaStateJson() const {
    std::lock_guard<std::mutex> lock(ota_mutex_);
    auto j=cJSON_CreateObject();
    cJSON_AddBoolToObject(j,"supported",true);cJSON_AddBoolToObject(j,"active",ota_running_.load());
    const int recovery=BootRecoveryState();
    cJSON_AddBoolToObject(j,"recovery",recovery>=0);
    cJSON_AddNumberToObject(j,"recoveryState",recovery);
    const bool version_fresh=LinkFresh() && installed_ota_version_.load()>0 &&
        xTaskGetTickCount()-installed_ota_tick_.load()<=pdMS_TO_TICKS(15000);
    cJSON_AddNumberToObject(j,"installedVersion",version_fresh?installed_ota_version_.load():0);
    cJSON_AddBoolToObject(j,"chassisReady",recovery>=0?recovery>0:ota_supported_.load() && LinkFresh());
    cJSON_AddStringToObject(j,"downloadOrigin",firmware_origin().c_str());
    cJSON_AddStringToObject(j,"jobId",ota_job_.c_str());cJSON_AddStringToObject(j,"phase",ota_phase_.c_str());
    cJSON_AddNumberToObject(j,"progress",ota_progress_);cJSON_AddStringToObject(j,"error",ota_error_.c_str());
    cJSON_AddStringToObject(j,"version",ota_version_.c_str());return dump(j);
}
int Stm32Link::BootRecoveryState() const {
    const int state=boot_recovery_state_.load();
    return state>=0 && xTaskGetTickCount()-boot_recovery_tick_.load()<=pdMS_TO_TICKS(2500)?state:-1;
}
void Stm32Link::OtaProgress(const char* phase,int progress,const std::string& error) {
    bool changed;
    {
        std::lock_guard<std::mutex> lock(ota_mutex_);
        changed = ota_phase_ != phase || ota_progress_ != progress || strcmp(phase, "queued") == 0;
        ota_phase_=phase;ota_progress_=progress;ota_error_=error;
        if(ota_phase_=="completed" || ota_phase_=="failed") {
            Settings saved("stm32_ota",true);saved.SetBool("pending",false);saved.SetString("phase",ota_phase_);
            saved.SetString("version",ota_version_);saved.SetString("error",ota_error_);
        }
    }
    // 只在阶段或整数百分比变化时刷新；释放状态锁后再获取显示锁。
    if (changed) Board::GetInstance().GetDisplay()->SetUpgradeProgress("STM32", phase, progress);
}
std::string Stm32Link::StartOta(const std::string& job,const std::string& url,const std::string& metadata) {
    if(job.empty()||job.size()>64||url.size()>512||metadata.size()>1024||!started_) return "{\"error\":\"升级请求无效或底盘链路未初始化\"}";
    {
        std::lock_guard<std::mutex> lock(ota_mutex_);
        if(job==ota_job_) {} // A repeated HTTP/MCP request must never start twice.
        else if(!ota_running_.exchange(true)) {
            ota_job_=job;ota_phase_="queued";ota_error_.clear();ota_progress_=0;ota_version_.clear();
            {Settings saved("stm32_ota",true);saved.SetString("job",job);saved.SetString("version","");saved.SetBool("pending",true);}
            auto work=new(std::nothrow) OtaWork{this,job,url,metadata};
            if(!work || xTaskCreate(OtaTask,"stm32_ota",8192,work,2,nullptr)!=pdPASS) {
                delete work;ota_running_=false;ota_phase_="failed";ota_error_="无法创建升级任务";
                Settings saved("stm32_ota",true);saved.SetBool("pending",false);saved.SetString("phase",ota_phase_);saved.SetString("error",ota_error_);
            }
        } else return "{\"error\":\"已有底盘升级正在进行，请刷新进度\"}";
    }
    return GetOtaStateJson();
}
void Stm32Link::OtaTask(void* context) {
    {
        std::unique_ptr<OtaWork> work(static_cast<OtaWork*>(context));work->self->RunOta(*work);
    }
    vTaskDelete(nullptr);
}
void Stm32Link::RunOta(const OtaWork& work) {
    bool uart_owned=false;
    auto cleanup=[&](void*) {
        if(uart_owned) {
            rx_length_=0;rx_overflow_=true;last_ping_tick_=xTaskGetTickCount()-pdMS_TO_TICKS(2000);
            xSemaphoreGive(uart_mutex_);
        }
        ota_exclusive_=false;ota_running_=false;
    };
    std::unique_ptr<void,decltype(cleanup)> guard(this,cleanup);
    OtaProgress("queued", 0);
    try {
        std::unique_ptr<cJSON,decltype(&cJSON_Delete)> meta(cJSON_Parse(work.metadata.c_str()),cJSON_Delete);
        if(!meta)throw std::runtime_error("固件元数据无效");
        uint32_t size=integer(meta.get(),"image_size",110592),version=integer(meta.get(),"firmware_version",UINT32_MAX);
        uint32_t expected_crc=integer(meta.get(),"crc32_value",UINT32_MAX);
        if(size<8 || (size&1) || !version || integer(meta.get(),"ota_application_abi",1)!=1 || text(meta.get(),"board")!="YB-EST01-V2.0" ||
           text(meta.get(),"mcu")!="STM32F103RCT6" || integer(meta.get(),"load_address",UINT32_MAX)!=0x08008000U)
            throw std::runtime_error("固件不适用于当前底盘");
        const auto origin=firmware_origin();
        const std::string prefix=origin+"/xiaozhi/ota/stm32/download/";
        if((origin.rfind("http://",0)!=0 && origin.rfind("https://",0)!=0) || work.url.rfind(prefix,0)!=0 ||
           work.url.size()!=prefix.size()+32 || work.url.find_first_not_of("0123456789abcdef",prefix.size())!=std::string::npos)
            throw std::runtime_error("下载地址与设备配置的 OTA 服务不一致");
        {
            std::lock_guard<std::mutex> lock(ota_mutex_);ota_version_=std::to_string(version);
        }
        const bool recovery_at_start=BootRecoveryState()>0;
        if(!ota_supported_ && !recovery_at_start)throw std::runtime_error("底盘尚未报告网页升级能力，请先烧录首次安装整包并等待状态更新");
        if(!LinkFresh() || request_busy_ || (!recovery_at_start && (calibration_active_ || tuning_pending_ || app_bluetooth_id_)))
            throw std::runtime_error("底盘离线或正在执行操作，请稍后升级");
        std::unique_ptr<uint8_t,decltype(&heap_caps_free)> image(
            static_cast<uint8_t*>(heap_caps_malloc(size,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)),heap_caps_free);
        if(!image)throw std::runtime_error("固件下载缓存不足");
        OtaProgress("downloading",1);
        auto http=Board::GetInstance().GetNetwork()->CreateHttp(0);http->SetTimeout(15000);
        if(!http->Open("GET",work.url) || http->GetStatusCode()!=200 || http->GetBodyLength()!=size)
            throw std::runtime_error("固件下载失败，请重新发起升级");
        for(size_t received=0;received<size;) {
            int n=http->Read(reinterpret_cast<char*>(image.get()+received),std::min<size_t>(4096,size-received));
            if(n<=0)throw std::runtime_error("固件下载不完整");
            received+=n;OtaProgress("downloading",static_cast<int>(received*15/size));
        }
        http->Close();
        unsigned char hash[32];
        if(mbedtls_sha256(image.get(),size,hash,0)!=0)throw std::runtime_error("固件哈希计算失败");
        char sha[65];for(int i=0;i<32;++i)snprintf(sha+i*2,3,"%02x",hash[i]);
        uint32_t stack,entry;memcpy(&stack,image.get(),4);memcpy(&entry,image.get()+4,4);
        if(crc32(image.get(),size)!=expected_crc || text(meta.get(),"sha256")!=sha ||
           stack<=0x20000000U || stack>0x2000C000U || (stack&7) || !(entry&1) ||
           (entry&~1U)<0x08008008U || (entry&~1U)>=0x08008000U+size)
            throw std::runtime_error("固件完整性或启动向量校验失败");
        OtaProgress("preparing",18);
        const bool recovery=BootRecoveryState()>0;
        if(recovery && !cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(meta.get(),"recovery_confirmed")))
            throw std::runtime_error("恢复升级前请确认车体已固定、供电稳定；当前无法读取电压");
        if(!recovery) {
        if(GetBluetoothControlState()<0)throw std::runtime_error("蓝牙控制状态未知，请刷新底盘状态");
        if(GetBluetoothControlState()==1) {
            auto result=RequestMotion("BT,0",0,true);
            if(result.find("手机移动控制已关闭")!=0)throw std::runtime_error("未确认退出蓝牙控制");
        }
        auto rest=RequestMotion("REST",0,true);
        if(rest!="休息完成。" && rest!="我已经坐下了。")throw std::runtime_error("未确认底盘坐下，升级未开始");
        bool seated=false;
        for(int i=0;i<30;++i) {
            vTaskDelay(pdMS_TO_TICKS(100));
            std::lock_guard<std::mutex> lock(motion_mutex_);
            seated=posture_seen_ && posture_state_==2 && LinkFresh() &&
                xTaskGetTickCount()-posture_tick_<=pdMS_TO_TICKS(1000) && BatteryFresh() && balance_stopped_ && battery_mv_>=10800;
            if(seated)break;
        }
        if(!seated)throw std::runtime_error("未确认停稳或电压低于 10.8V，升级未开始");
        }
        ota_exclusive_=true;
        if(xSemaphoreTake(uart_mutex_,pdMS_TO_TICKS(2000))!=pdTRUE)throw std::runtime_error("串口正在使用，升级未开始");
        uart_owned=true;uart_flush_input(kUart);xQueueReset(uart_events_);
        if(!recovery) WriteFrame("OTA_ENTER,1");
        uint32_t session=esp_random()|1U,sequence=0;
        auto call=[&](const std::string& command,int timeout,int retries) {
            ++sequence;
            const auto identity="OTA,1,"+std::to_string(session)+","+std::to_string(sequence)+",";
            const auto payload=identity+command;
            char checksum[8];snprintf(checksum,sizeof(checksum),"*%04X\n",link_crc16(payload.data(),payload.size()));
            const auto wire="@"+payload+checksum;
            for(int retry=0;retry<retries;++retry) {
                if(uart_write_bytes(kUart,wire.data(),wire.size())!=static_cast<int>(wire.size()))throw std::runtime_error("串口发送失败");
                auto deadline=esp_timer_get_time()+static_cast<int64_t>(timeout)*1000;
                char line[160];size_t used=0;bool collecting=false;
                while(esp_timer_get_time()<deadline) {
                    uint8_t byte;
                    if(uart_read_bytes(kUart,&byte,1,pdMS_TO_TICKS(20))!=1)continue;
                    if(byte=='@'){collecting=true;used=0;}
                    else if(collecting && byte=='\n') {
                        collecting=false;
                        if(link_decode(line,used)!=1)continue;
                        std::string reply=line;
                        if(reply=="ERR,1,OTA_UNSAFE")throw std::runtime_error("底盘未满足停稳或供电条件，未进入升级模式");
                        if(reply.rfind(identity,0)!=0)continue;
                        auto fields=split(reply);
                        if(fields.size()==6 && fields[4]=="ERR")throw std::runtime_error("Bootloader 拒绝升级步骤："+fields[5]);
                        if(fields.size()==8 && fields[4]=="OK")return std::array<uint32_t,3>{number(fields[5]),number(fields[6]),number(fields[7])};
                    } else if(collecting) {
                        if(used+1>=sizeof(line))collecting=false;
                        else line[used++]=byte;
                    }
                }
            }
            throw std::runtime_error("串口升级响应超时，结果未确认，请查看底盘状态");
        };
        OtaProgress("bootloader",22);
        auto hello=call("HELLO",2000,6);
        if(recovery && hello[1]==4) {
            OtaProgress("recovering",23);
            try { hello=call("INSTALL",20000,3); }
            catch(const std::exception& failure) {
                if(std::string(failure.what())!="Bootloader 拒绝升级步骤：INSTALL")throw;
                try {hello=call("ABORT",10000,3);}
                catch(const std::exception& abort_failure) {
                    if(std::string(abort_failure.what())!="Bootloader 拒绝升级步骤：ABORT")throw;
                    // BEGIN below is accepted only if staging is damaged.
                }
            }
            if(hello[1]==1 && hello[2]>=version) {
                try {call("RESET",2000,1);}catch(const std::exception&){}
                throw std::runtime_error("底盘已恢复至版本 "+std::to_string(hello[2])+"，请刷新状态；本次固件未重复安装");
            }
        }
        if(hello[2]>=version)throw std::runtime_error("固件版本必须高于底盘已安装版本");
        call("BEGIN,YB-EST01-V2.0,"+std::to_string(version)+","+std::to_string(size)+","+
             std::to_string(expected_crc)+",134250496",10000,3);
        for(uint32_t offset=0;offset<size;) {
            const uint32_t count=std::min<uint32_t>(256,size-offset);char hex[513];
            for(uint32_t i=0;i<count;++i)snprintf(hex+i*2,3,"%02X",image.get()[offset+i]);
            auto ack=call("DATA,"+std::to_string(offset)+","+hex,2000,3);
            offset+=count;if(ack[0]!=offset)throw std::runtime_error("接收偏移未确认，已停止升级");
            OtaProgress("transferring",25+static_cast<int>(offset*60/size));
        }
        call("END",10000,3);OtaProgress("installing",90);
        auto installed=call("INSTALL",20000,3);
        if(installed[1]!=1 || installed[2]!=version)throw std::runtime_error("新固件安装未确认");
        // A reset can discard its own ACK. Installation is already verified;
        // fresh post-reset application telemetry is the final authority.
        try {call("RESET",2000,1);} catch(const std::exception&) {}
        OtaProgress("verifying",95);
        connected_=false;status_valid_=false;
        {std::lock_guard<std::mutex> lock(motion_mutex_);posture_seen_=false;motion_seen_=false;}
        rx_length_=0;rx_overflow_=true;
        const auto deadline=esp_timer_get_time()+20000000;
        bool booted=false;
        while(esp_timer_get_time()<deadline) {
            uint8_t data[256];int n=uart_read_bytes(kUart,data,sizeof(data),pdMS_TO_TICKS(50));
            for(int i=0;i<n;++i)FeedByte(data[i]);
            {std::lock_guard<std::mutex> lock(motion_mutex_);
                booted=LinkFresh()&&BatteryFresh()&&posture_seen_&&posture_state_==2;
            }
            if(booted)break;
        }
        if(!booted)throw std::runtime_error("固件已安装，但应用启动尚未确认，请查看底盘");
        OtaProgress("completed",100);
    } catch(const std::exception& error) {OtaProgress("failed",0,error.what());}
    catch(...) {OtaProgress("failed",0,"升级异常，执行结果未确认");}
}
