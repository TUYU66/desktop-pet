"""Exercise production posture snapshots and result recovery with host ports."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / 'xiaozhi-esp32-main/main/boards/bread-compact-wifi-lcd'


class ChassisStateTests(unittest.TestCase):
    def test_motion_admission_uses_request_lease_not_late_busy_telemetry(self):
        source = (BOARD / 'stm32_link_motion.cc').read_text(encoding='utf-8')
        start = source.index('std::string Stm32Link::RequestMotion(')
        end = source.index('    {\n        std::lock_guard<std::mutex> lock(motion_mutex_);\n        const bool fresh = posture_seen_', start)
        admission = source[start:end] + '    return "admitted";\n}\n'
        self.run_cpp(r'''
#include <cassert>
#include <atomic>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
using TickType_t=unsigned;
struct Semaphore {bool held=false;};
constexpr int pdTRUE=1, portMAX_DELAY=-1;
unsigned pdMS_TO_TICKS(unsigned n){return n;}
unsigned xTaskGetTickCount(){return 1000;}
int xSemaphoreTake(Semaphore* s,int){if(s->held)return 0;s->held=true;return 1;}
void xSemaphoreGive(Semaphore* s){s->held=false;}
#define ESP_LOGE(...) ((void)0)
const char* kTag="test";
struct Stm32Link {
    Semaphore request,uart,reply;
    Semaphore* uart_mutex_=&uart;
    Semaphore* request_mutex_=&request;
    Semaphore* reply_semaphore_=&reply;
    std::atomic<bool> started_{true},request_busy_{false},calibration_active_{false};
    std::atomic<bool> ota_running_{false};
    std::atomic<unsigned> pending_sequence_{0},pending_kind_{0};
    std::mutex tuning_mutex_;
    bool gyro_seen_=false,fresh=true;
    int gyro_fields_[13]{};
    unsigned gyro_tick_=0;
    bool LinkFresh(){return fresh;}
    int recovery=-1;
    int BootRecoveryState() const {return recovery;}
    struct Snapshot {bool busy=true;};
    Snapshot GetMotionSnapshot(){return {};}
    void FinishAction(const char*){request_busy_=false;}
    std::string RequestMotion(const char*,unsigned=0,bool=false);
};
ADMISSION
int main(){
    Stm32Link link;
    assert(link.GetMotionSnapshot().busy); // old queued TURN phase
    assert(link.RequestMotion("REST")=="admitted");
    link.ota_running_=true;
    assert(link.RequestMotion("REST")!="admitted");
    assert(link.RequestMotion("REST",0,true)=="admitted");
    link.ota_running_=false;
    assert(!link.request.held&&!link.uart.held);
    link.request.held=true;
    assert(link.RequestMotion("REST")!="admitted");
    link.request.held=false;
    link.fresh=false;assert(link.RequestMotion("REST")!="admitted");
    link.fresh=true;link.recovery=4;
    assert(link.RequestMotion("REST",0,true)!="admitted");
    link.recovery=-1;link.calibration_active_=true;
    assert(link.RequestMotion("REST")!="admitted");
}
'''.replace('ADMISSION', admission))

    def test_boot_recovery_beacon_requires_valid_fields_and_expires(self):
        source = (BOARD / 'stm32_link_receive.cc').read_text(encoding='utf-8')
        start = source.index('void Stm32Link::ProcessLine(')
        branch = source[start:source.index('    if (strncmp(line, "POSE,", 5)', start)] + '}\n'
        source = (BOARD / 'stm32_link_protocol.cc').read_text(encoding='utf-8')
        start = source.index('bool ParseFields(')
        parser = source[start:source.index('\n}\n', start) + 2]
        source = (BOARD / 'stm32_link_ota.cc').read_text(encoding='utf-8')
        start = source.index('int Stm32Link::BootRecoveryState() const')
        freshness = source[start:source.index('\n}\n', start) + 2]
        self.run_cpp(r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <atomic>
#include <mutex>
#include <cerrno>
using TickType_t=unsigned;
unsigned now=1000;
unsigned xTaskGetTickCount(){return now;}
unsigned pdMS_TO_TICKS(unsigned n){return n;}
PARSER
struct Stm32Link {
    std::atomic<int> boot_recovery_state_{-1};
    std::atomic<unsigned> boot_recovery_tick_{0};
    bool status_valid_=true,posture_seen_=true,motion_seen_=true;
    unsigned frames=0;
    std::mutex motion_mutex_;
    void MarkFrame(){frames++;}
    void ProcessLine(const char*);
    int BootRecoveryState() const;
};
BRANCH
FRESHNESS
int main(){
    Stm32Link link;
    assert(link.BootRecoveryState()==-1);
    link.ProcessLine("BOOT_STATE,3,4,1");
    assert(link.BootRecoveryState()==4 && link.frames==1);
    assert(!link.status_valid_&&!link.posture_seen_&&!link.motion_seen_);
    link.ProcessLine("BOOT_STATE,3,4,0");
    link.ProcessLine("BOOT_STATE,3,5,1");
    link.ProcessLine("BOOT_STATE,2,4,1");
    link.ProcessLine("BOOT_STATE,3,4,4294967296");
    assert(link.frames==1);
    now+=2501;assert(link.BootRecoveryState()==-1);
    link.ProcessLine("BOOT_STATE,3,0,0");assert(link.BootRecoveryState()==0);
}
'''.replace('PARSER', parser).replace('BRANCH', branch).replace('FRESHNESS', freshness))

    def test_runtime_diagnostics_validate_units_and_counter_ranges(self):
        source = (BOARD / 'stm32_link_receive.cc').read_text(encoding='utf-8')
        start = source.index('        } else if (strncmp(line, "DIAG,RTOS,", 10) == 0)')
        end = source.index('        } else if (strncmp(line, "DIAG,CTRL,", 10) == 0)', start)
        branches = source[start:end].replace('} else if', 'if', 1) + '\n        }\n'
        source = (BOARD / 'stm32_link_protocol.cc').read_text(encoding='utf-8')
        start = source.index('bool ParseFields(')
        parser = source[start:source.index('\n}\n', start) + 2]
        self.run_cpp(r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cerrno>
PARSER
bool valid_diagnostic(const char* line) {
    int64_t fields[15]{};
    bool valid=false;
    BRANCHES
    return valid;
}
int main() {
    assert(valid_diagnostic("DIAG,RTOS,1,5000,400,320,480,9000,8500"));
    assert(valid_diagnostic("DIAG,RTOS,1,4294967295,400,320,480,9000,8500"));
    assert(!valid_diagnostic("DIAG,RTOS,2,5000,400,320,480,9000,8500"));
    assert(!valid_diagnostic("DIAG,RTOS,1,5000,-1,320,480,9000,8500"));
    assert(!valid_diagnostic("DIAG,RTOS,1,5000,400,320,480,9000,9500"));
    assert(!valid_diagnostic("DIAG,RTOS,1,5000,400,320,480,9000,8500,0"));
    assert(valid_diagnostic("DIAG,TASKS,1,5000,1400,900,700,800"));
    assert(!valid_diagnostic("DIAG,TASKS,1,5000,1400,-1,700,800"));
    assert(!valid_diagnostic("DIAG,TASKS,2,5000,1400,900,700,800"));
    assert(valid_diagnostic("DIAG,QUEUE,1,0,20,0,3,0"));
    assert(!valid_diagnostic("DIAG,QUEUE,1,0,20,0,3,-1"));
    assert(!valid_diagnostic("DIAG,QUEUE,1,0,20,0,3,0,0"));
    assert(valid_diagnostic("DIAG,IRQ,1,5000,1000,4998,5010,1500,0"));
    assert(valid_diagnostic("DIAG,IRQ,1,5000,0,0,0,0,0"));
    assert(!valid_diagnostic("DIAG,IRQ,1,5000,0,0,0,10,0"));
    assert(!valid_diagnostic("DIAG,IRQ,1,5000,1,6000,5000,10,0"));
    assert(!valid_diagnostic("DIAG,IRQ,1,5000,1,5000,5000,10,2"));
    assert(!valid_diagnostic("DIAG,IRQ,1,5000,1,5000,5000,-1,0"));
}
'''.replace('PARSER', parser).replace('BRANCHES', branches))

    def run_cpp(self, source):
        compiler = os.environ.get('CXX') or shutil.which('g++')
        if not compiler:
            self.skipTest('Host C++ compiler required')
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / 'test.cpp'
            binary = Path(directory) / 'test.exe'
            cpp.write_text(source, encoding='utf-8')
            subprocess.run([compiler, '-std=c++14', '-finput-charset=UTF-8', '-fexec-charset=UTF-8', str(cpp), '-o', str(binary)],
                           check=True, capture_output=True, timeout=40)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)

    def test_repeated_bluetooth_voice_entry_requires_fresh_mode_and_pose(self):
        source = (BOARD / 'stm32_link.cc').read_text(encoding='utf-8')
        start = source.index('int Stm32Link::GetBluetoothControlState() const')
        getter = source[start:source.index('\n}\n', start) + 2]
        source = (BOARD / 'stm32_link_motion.cc').read_text(encoding='utf-8')
        start = source.index('    {\n        std::lock_guard<std::mutex> lock(motion_mutex_);\n        const bool fresh = posture_seen_')
        guard = source[start:source.index('    const char* action', start)]
        self.run_cpp(r'''
#include <cassert>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>
using TickType_t=uint32_t;
TickType_t tick=1000;
TickType_t xTaskGetTickCount(){return tick;}
TickType_t pdMS_TO_TICKS(int n){return n;}
struct Stm32Link {
    bool connected=true, posture_seen_=true;
    mutable std::mutex motion_mutex_;
    int posture_state_=1;
    TickType_t posture_tick_=1000;
    std::atomic<int> bluetooth_mode_{1};
    std::atomic<TickType_t> bluetooth_tick_{1000};
    bool LinkFresh() const {return connected;}
    int GetBluetoothControlState() const;
    std::string request(const char* command,unsigned app_request=0) {
        bool standing=!strcmp(command,"STAND");
        GUARD
        return "send";
    }
};
GETTER
int main() {
    Stm32Link link;
    assert(link.request("BT,1")=="蓝牙控制已经开启，可以直接使用 App 操作。");
    assert(link.request("BT,1",12)=="send"); // App still needs its handshake
    assert(link.request("BT,0")=="send"); // Exit is never suppressed
    link.bluetooth_mode_=0; assert(link.request("BT,1")=="send");
    link.bluetooth_mode_=1;link.posture_state_=2;assert(link.request("BT,1")=="send");
    link.posture_state_=1;tick=2501;assert(link.request("BT,1")=="send");
    link.posture_tick_=tick;assert(link.request("BT,1")=="send"); // stale mode
    link.bluetooth_tick_=tick;link.connected=false;assert(link.request("BT,1")=="send");
}
'''.replace('GUARD', guard).replace('GETTER', getter))

    def test_app_permission_waits_for_matching_server_speech_ack(self):
        source = (BOARD / 'stm32_link.cc').read_text(encoding='utf-8')
        methods = []
        for signature in ('void Stm32Link::OnBluetoothSpeechResult(', 'bool Stm32Link::AnnounceAppBluetooth('):
            start = source.index(signature)
            methods.append(source[start:source.index('\n}\n', start) + 2])
        self.run_cpp(r'''
#define ESP_LOGI(...) ((void)0)
#include <atomic>
#include <cassert>
#include <cstdint>
using TickType_t=uint32_t;
TickType_t tick=0;
TickType_t xTaskGetTickCount(){return tick;}
TickType_t pdMS_TO_TICKS(int n){return n;}
void vTaskDelay(int n){tick+=n;}
std::atomic<unsigned> bluetooth_notice_id{0};
std::atomic<int> bluetooth_notice_result{0};
int canceled=0,server_ack=1,events=0;
void CancelBluetoothAnnouncement(){bluetooth_notice_id=0;++canceled;}
struct Audio {bool available=true;bool WaitForPlaybackQueueEmpty(int){return available;}};
struct Application {
    Audio audio;
    static Application& GetInstance(){static Application app;return app;}
    Audio& GetAudioService(){return audio;}
    void SendBluetoothControlEvent(unsigned,bool,bool){++events;bluetooth_notice_result=server_ack;}
};
struct AppBluetoothRequest{unsigned id;bool enabled;};
struct Stm32Link {
    std::atomic<unsigned> app_bluetooth_id_{7};
    void OnBluetoothSpeechResult(unsigned,bool);
    bool AnnounceAppBluetooth(const AppBluetoothRequest&,bool);
};
METHODS
int main() {
    Stm32Link link;
    bluetooth_notice_id=7;
    link.OnBluetoothSpeechResult(8,true);assert(bluetooth_notice_result==0);
    link.OnBluetoothSpeechResult(7,true);assert(bluetooth_notice_result==1);
    assert(link.AnnounceAppBluetooth({7,true},true)&&bluetooth_notice_id==0);
    server_ack=-1;assert(!link.AnnounceAppBluetooth({7,true},true)&&canceled==1);
    server_ack=0;tick=0;assert(!link.AnnounceAppBluetooth({7,true},true)&&tick>=15000&&canceled==2);
    server_ack=1;Application::GetInstance().audio.available=false;
    assert(!link.AnnounceAppBluetooth({7,true},true));
    int before=events;assert(!link.AnnounceAppBluetooth({8,true},true)&&events==before);
}
'''.replace('METHODS', '\n'.join(methods)))

    def test_lcd_bluetooth_icon_tracks_mode_beside_wifi(self):
        source = (ROOT / 'xiaozhi-esp32-main/main/display/lcd_display.cc').read_text(encoding='utf-8')
        start = source.index('void LcdDisplay::UpdateStatusBar(bool update_all)')
        method = source[start:source.index('\n}\n', start) + 2]
        self.run_cpp(r'''
#include <cassert>
#include <cstdio>
#include <string>
struct lv_obj_t {std::string text;bool hidden=false;int x=0,y=0,color=0;lv_obj_t* parent=nullptr;};
#define FONT_AWESOME_BLUETOOTH "bluetooth-glyph"
enum {LV_PART_MAIN=0,LV_OPA_TRANSP=0,LV_OBJ_FLAG_HIDDEN=1,LV_OBJ_FLAG_FLOATING=2,LV_ALIGN_OUT_RIGHT_MID=3};
lv_obj_t* lv_label_create(lv_obj_t* parent){auto* p=new lv_obj_t;p->parent=parent;return p;}
void lv_label_set_text(lv_obj_t* p,const char* s){p->text=s;}
void lv_obj_add_flag(lv_obj_t* p,int f){if(f==LV_OBJ_FLAG_HIDDEN)p->hidden=true;}
void lv_obj_remove_flag(lv_obj_t* p,int){p->hidden=false;}
bool lv_obj_has_flag(lv_obj_t* p,int){return p->hidden;}
void lv_obj_align_to(lv_obj_t* p,lv_obj_t* target,int align,int x,int y){
    assert(align==LV_ALIGN_OUT_RIGHT_MID);p->x=target->x+x;p->y=target->y+y;
}
void lv_obj_update_layout(lv_obj_t*){}
const int* lv_obj_get_style_text_font(lv_obj_t*,int){static int font;return &font;}
int lv_color_hex(int n){return n;}
void lv_obj_set_style_text_color(lv_obj_t* p,int color,int){p->color=color;}
#define STYLE(name) template<class... Args>void name(Args...){}
STYLE(lv_obj_set_style_text_font)
STYLE(lv_obj_set_style_pad_all)
STYLE(lv_obj_set_style_bg_opa)
STYLE(lv_obj_set_style_border_width)
STYLE(lv_obj_set_style_outline_width)
int mode=0;
struct Board {
    static Board& GetInstance(){static Board board;return board;}
    bool GetBatteryLevel(int&,bool&,bool&){return false;}
    int GetBluetoothControlState(){return mode;}
};
struct DisplayLockGuard {template<class T>DisplayLockGuard(T*){}};
struct LvglDisplay {void UpdateStatusBar(bool){}};
struct LcdDisplay:LvglDisplay {
    lv_obj_t top,battery,wifi;
    lv_obj_t* top_bar_=&top;
    lv_obj_t* network_label_=&wifi;
    lv_obj_t* bluetooth_icon_label_=nullptr;
    lv_obj_t* battery_percent_label_=&battery;
    lv_obj_t* battery_label_=nullptr;
    const char* battery_icon_=nullptr;
    void UpdateStatusBar(bool);
};
METHOD
int main() {
    LcdDisplay display;
    display.wifi.x=10;display.wifi.y=4;
    display.UpdateStatusBar(false);assert(!display.bluetooth_icon_label_);
    mode=1;display.UpdateStatusBar(false);
    auto* icon=display.bluetooth_icon_label_;
    assert(icon&&!icon->hidden&&icon->text==FONT_AWESOME_BLUETOOTH);
    assert(icon->parent==display.top_bar_&&icon->x==16&&icon->y==4&&icon->color==0x75C9FF);
    mode=-1;display.UpdateStatusBar(false);assert(!icon->hidden&&icon->color==0xF0C674);
    mode=0;display.UpdateStatusBar(false);assert(icon->hidden);
    mode=-1;display.UpdateStatusBar(false);assert(icon->hidden);
    mode=1;display.wifi.y=7;display.UpdateStatusBar(false);
    assert(display.bluetooth_icon_label_==icon&&!icon->hidden&&icon->y==7);
    delete icon;
}
'''.replace('METHOD', method))

    def test_controller_posture_is_not_merely_the_motor_stop_flag(self):
        source = (ROOT / 'stm32/Balance_Car_KEil_HAL/APP/app_control.c').read_text(encoding='utf-8')
        start = source.index('void Chassis_FormatState(')
        method = source[start:source.index('\n}\n', start) + 2]
        self.run_cpp(r'''
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
using u8 = uint8_t;
unsigned __get_PRIMASK() {return 0;}
void __disable_irq() {}
void __set_PRIMASK(unsigned) {}
uint32_t tick=1000;
uint32_t HAL_GetTick() {return tick;}
u8 Stop_Flag=1, bluetooth_control_enabled=0, chassis_rest_state=0;
u8 angle_sample_valid=1, calibration_active=0, gyro_active=0;
unsigned rear_support_samples=0, turn_state=0, balance_rise_pending=0;
uint32_t angle_last_update_ms=1000;
enum {TURN_RISING=1};
u8 Chassis_GyroActive() {return gyro_active;}
METHOD
int main() {
    char text[100];
    Chassis_FormatState(text,sizeof(text));
    assert(!strcmp(text,"POSE,1,1000,0,1,0\n")); // stopped != seated
    rear_support_samples=40;
    Chassis_FormatState(text,sizeof(text));
    assert(!strcmp(text,"POSE,1,1000,2,1,0\n")); // seated after power-on, rest=0
    Stop_Flag=0; rear_support_samples=0;
    Chassis_FormatState(text,sizeof(text));
    assert(!strcmp(text,"POSE,1,1000,1,0,0\n")); // standing != Bluetooth enabled
    bluetooth_control_enabled=1;
    Chassis_FormatState(text,sizeof(text));
    assert(!strcmp(text,"POSE,1,1000,1,0,1\n"));
    balance_rise_pending=1;
    Chassis_FormatState(text,sizeof(text));
    assert(strstr(text,",3,0,1\n"));
    balance_rise_pending=0; turn_state=TURN_RISING;
    Chassis_FormatState(text,sizeof(text));
    assert(strstr(text,",3,0,1\n"));
    chassis_rest_state=1;
    Chassis_FormatState(text,sizeof(text));
    assert(strstr(text,",4,0,1\n"));
    tick=1151;
    Chassis_FormatState(text,sizeof(text));
    assert(strstr(text,",0,0,1\n")); // stale IMU -> unknown
    chassis_rest_state=3; Stop_Flag=1;
    Chassis_FormatState(text,sizeof(text));
    assert(strstr(text,",5,1,1\n"));
}
'''.replace('METHOD', method))

    def test_result_query_recovers_completed_pending_and_missing_records(self):
        source = (ROOT / 'stm32/Balance_Car_KEil_HAL/BSP/ChassisLink/chassis_link.c').read_text(encoding='utf-8')
        start = source.index('    else if (strncmp(line, "POSTURE_RESULT,"')
        end = source.index('\n}\n', start)
        branch = source[start:end].replace('    else if', '    if', 1)
        self.run_cpp(r'''
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
char line[100], posture_result[100]="BT_DONE,42,1,0\n";
unsigned posture_result_sequence=42, posture_sequence=0;
uint32_t tick=1000, posture_result_ms=900;
uint32_t HAL_GetTick() {return tick;}
std::string sent;
void send_line(const char* text) {sent=text;}
void stand_reply(unsigned, const char*) {sent="ACK";}
int parse_sequence(const char* text,unsigned* value) {
    if(!*text)return 0;
    unsigned long n=0;
    while(*text){if(*text<'0'||*text>'9')return 0;n=n*10+*text++-'0';if(n>65535)return 0;}
    *value=n;return 1;
}
void query(const char* value) {strcpy(line,value);sent.clear();BRANCH}
int main() {
    query("POSTURE_RESULT,42"); assert(sent=="BT_DONE,42,1,0\n");
    posture_sequence=43;
    query("POSTURE_RESULT,43"); assert(sent=="ACK");
    query("POSTURE_RESULT,44"); assert(sent=="POSTURE_UNKNOWN,44\n");
    tick=60900;
    query("POSTURE_RESULT,42"); assert(sent=="POSTURE_UNKNOWN,42\n");
    query("POSTURE_RESULT,0"); assert(sent.empty());
    query("POSTURE_RESULT,42x"); assert(sent.empty());
}
'''.replace('BRANCH', branch))

    def test_esp_snapshot_expires_without_falling_back_to_old_diagnostics(self):
        source = (BOARD / 'stm32_link.cc').read_text(encoding='utf-8')
        start = source.index('std::string Stm32Link::GetStatusJson() const')
        method = source[start:source.index('\n}\n', start) + 2]
        self.run_cpp(r'''
#include <cassert>
#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
using TickType_t=uint32_t;
TickType_t tick=1000;
TickType_t xTaskGetTickCount(){return tick;}
TickType_t pdMS_TO_TICKS(int n){return n;}
int EstimateBatteryPercent(int){return 50;}
struct Stm32Link {
    bool connected=true;
    mutable std::mutex motion_mutex_;
    bool motion_seen_=true, motion_stopped_=false, posture_seen_=true;
    TickType_t motion_tick_=1000, posture_tick_=1000;
    int motion_phase_=0, motion_error_=0, motion_rest_=0, posture_state_=1;
    std::atomic<int> bluetooth_mode_{0},battery_mv_{11400},balance_stopped_{0},low_battery_{0};
    std::atomic<TickType_t> bluetooth_tick_{1000};
    struct Motion {std::string action="",phase="idle",result="unknown";bool busy=false,valid=true;};
    Motion GetMotionSnapshot() const{return {};}
    bool LinkFresh() const{return connected;}
    bool BatteryFresh() const{return connected;}
    std::string GetCalibrationJson() const{return "{}";}
    std::string GetStatusJson() const;
};
METHOD
int main() {
    Stm32Link link;
    auto state=link.GetStatusJson();
    assert(state.find("\"posture\":\"standing\"")!=std::string::npos);
    assert(state.find("\"bluetoothMode\":false")!=std::string::npos);
    tick=2501; link.motion_tick_=tick; // DIAG is fresh, POSE is stale
    state=link.GetStatusJson();
    assert(state.find("\"posture\":\"unknown\"")!=std::string::npos);
    assert(state.find("\"bluetoothMode\":null")!=std::string::npos);
    link.posture_tick_=tick;link.posture_state_=2;
    state=link.GetStatusJson();
    assert(state.find("\"posture\":\"seated\"")!=std::string::npos);
    link.connected=false;
    assert(link.GetStatusJson().find("\"posture\":\"unknown\"")!=std::string::npos);
}
'''.replace('METHOD', method))


if __name__ == '__main__':
    unittest.main()
