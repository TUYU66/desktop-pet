"""Execute the production App entry sequence against fake posture/audio ports."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BluetoothEntryTests(unittest.TestCase):
    def test_stance_then_prompt_then_completion_and_failure_fences(self):
        compiler = os.environ.get('CXX') or shutil.which('g++')
        if not compiler:
            self.skipTest('Host C++ compiler required')
        source = (ROOT / 'main/boards/bread-compact-wifi-lcd/stm32_link.cc').read_text(encoding='utf-8')
        start = source.index('void Stm32Link::RunAppBluetooth(')
        end = source.index('\n}\n', start) + 2
        method = source[start:end]
        harness = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>
#define ESP_LOGW(...) ((void)0)
using TickType_t = int;
int clock_tick;
std::function<void()> on_delay;
int xTaskGetTickCount() { return clock_tick++; }
int pdMS_TO_TICKS(int value) { return value; }
void vTaskDelay(int value) { clock_tick += value; if(on_delay)on_delay(); }
enum { kDeviceStateIdle, kDeviceStateListening, kDeviceStateSpeaking };
struct Audio { bool available=true; bool WaitForPlaybackQueueEmpty(int) { return available; } };
struct Application {
    Audio audio; int state=kDeviceStateIdle; bool voice=false;
    static Application& GetInstance() { static Application app; return app; }
    int GetDeviceState() { return state; }
    bool IsVoiceDetected() { return voice; }
    Audio& GetAudioService() { return audio; }
};
std::vector<std::string> events;
bool prompt_ok=true;
struct AppBluetoothRequest { unsigned id; bool enabled; };
struct Stm32Link {
    std::atomic<unsigned> app_bluetooth_id_{1}; bool cancel=false;
    std::string outcome="已经站稳，蓝牙控制已开启，可以用手机 App 操控了。";
    struct Snapshot { bool busy; };
    Snapshot GetMotionSnapshot() { return {false}; }
    std::string RequestMotion(const char*, unsigned) {
        events.push_back("motion"); if(cancel) app_bluetooth_id_=2; return outcome;
    }
    bool AnnounceAppBluetooth(const AppBluetoothRequest&, bool success) {
        events.push_back(success?"server-speech":"server-failure-speech");return prompt_ok;
    }
    void SendAppBluetoothResult(unsigned, bool success) { events.push_back(success?"success":"failed"); }
    void RunAppBluetooth(const AppBluetoothRequest& request);
};
METHOD
int main() {
    Stm32Link link;
    link.RunAppBluetooth({1,true});
    assert((events==std::vector<std::string>{"motion","server-speech","success"}));
    events.clear(); link.outcome="起立还没完成，蓝牙控制没有开启。";
    link.RunAppBluetooth({1,true});
    assert((events==std::vector<std::string>{"motion","failed","server-failure-speech"}));
    events.clear();link.outcome="已经站稳，蓝牙控制已开启，可以用手机 App 操控了。";prompt_ok=false;
    link.RunAppBluetooth({1,true});
    assert(events.back()=="failed");
    events.clear();prompt_ok=true;link.cancel=true;
    link.RunAppBluetooth({1,true});
    assert((events==std::vector<std::string>{"motion"}));
    events.clear();link.cancel=false;link.app_bluetooth_id_=1;
    Application::GetInstance().state=kDeviceStateSpeaking;
    clock_tick=0;
    link.RunAppBluetooth({1,true});
    assert((events==std::vector<std::string>{"failed"}));
    assert(clock_tick>=4000);
    // The voice-entry reply is just finishing: wait, then announce connection.
    events.clear();clock_tick=0;
    on_delay=[](){if(clock_tick>=100)Application::GetInstance().state=kDeviceStateIdle;};
    link.RunAppBluetooth({1,true});
    assert((events==std::vector<std::string>{"motion","server-speech","success"}));
    assert(clock_tick>=100 && clock_tick<4000);
    // A superseding exit cancels a pending ON without old speech or motion.
    events.clear();Application::GetInstance().state=kDeviceStateSpeaking;
    on_delay=[&link](){link.app_bluetooth_id_=2;};
    link.RunAppBluetooth({1,true});
    assert(events.empty());
    on_delay=nullptr;link.app_bluetooth_id_=1;
    events.clear();Application::GetInstance().state=kDeviceStateIdle;
    link.outcome="手机移动控制已关闭，保持站立。";
    link.RunAppBluetooth({1,false});
    assert((events==std::vector<std::string>{"motion","server-speech","success"}));
}
'''.replace('METHOD', method)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            cpp, binary = path / 'entry.cpp', path / 'entry.exe'
            cpp.write_text(harness, encoding='utf-8')
            subprocess.run([compiler, '-std=c++14', '-finput-charset=UTF-8', '-fexec-charset=UTF-8',
                            str(cpp), '-o', str(binary)], check=True, capture_output=True, timeout=40)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=5)
