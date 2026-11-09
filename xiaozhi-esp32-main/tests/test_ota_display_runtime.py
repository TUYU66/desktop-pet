"""Run the real LCD OTA method against an in-memory LVGL surface."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OtaDisplayTests(unittest.TestCase):
    def test_targets_progress_and_terminal_timer(self):
        source = (ROOT / 'main/display/lcd_appearance.cc').read_text(encoding='utf-8')
        method = source[source.index('void LcdDisplay::SetUpgradeProgress('):]
        program = r'''
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
struct lv_obj_t { std::string text; int value=0; bool hidden=false; };
struct lv_timer_t { void (*callback)(lv_timer_t*); void* user; };
enum { LV_OPA_COVER, LV_OBJ_FLAG_SCROLLABLE, LV_TEXT_ALIGN_CENTER,
       LV_ALIGN_TOP_MID, LV_PART_MAIN, LV_PART_INDICATOR, LV_ANIM_OFF,
       LV_OBJ_FLAG_HIDDEN };
int lv_color_hex(int n){return n;}
lv_obj_t screen;
lv_obj_t* lv_display_get_screen_active(void*){return &screen;}
lv_obj_t* lv_obj_create(lv_obj_t*){return new lv_obj_t;}
lv_obj_t* lv_label_create(lv_obj_t* p){return lv_obj_create(p);}
lv_obj_t* lv_bar_create(lv_obj_t* p){return lv_obj_create(p);}
template<class... T> void lv_obj_remove_style_all(T...){}
template<class... T> void lv_obj_set_size(T...){}
template<class... T> void lv_obj_center(T...){}
template<class... T> void lv_obj_set_style_bg_color(T...){}
template<class... T> void lv_obj_set_style_bg_opa(T...){}
template<class... T> void lv_obj_set_style_text_color(T...){}
template<class... T> void lv_obj_set_style_text_font(T...){}
template<class... T> void lv_obj_set_width(T...){}
template<class... T> void lv_obj_set_style_text_align(T...){}
template<class... T> void lv_obj_align(T...){}
template<class... T> void lv_obj_move_foreground(T...){}
template<class... T> void lv_bar_set_range(T...){}
void lv_obj_remove_flag(lv_obj_t* p,int f){if(f==LV_OBJ_FLAG_HIDDEN)p->hidden=false;}
void lv_obj_add_flag(lv_obj_t* p,int f){if(f==LV_OBJ_FLAG_HIDDEN)p->hidden=true;}
void lv_label_set_text(lv_obj_t* p,const char* text){p->text=text;}
void lv_bar_set_value(lv_obj_t* p,int n,int){p->value=n;}
int timers=0;
lv_timer_t* lv_timer_create(void(*cb)(lv_timer_t*),int ms,void* user){
    assert(ms==3000);++timers;return new lv_timer_t{cb,user};
}
void* lv_timer_get_user_data(lv_timer_t* p){return p->user;}
void lv_timer_delete(lv_timer_t* p){--timers;delete p;}
struct Font { void* font(){return nullptr;} };
struct LvglTheme { Font f; Font* text_font(){return &f;} };
struct DisplayLockGuard { template<class T> DisplayLockGuard(T*){} };
struct LcdDisplay {
    bool setup_ui_called_=true;void* display_=this;
    int width_=240,height_=240;LvglTheme theme;void* current_theme_=&theme;
    lv_obj_t *upgrade_panel_=nullptr,*upgrade_title_=nullptr,*upgrade_detail_=nullptr,
        *upgrade_progress_=nullptr,*upgrade_hint_=nullptr;
    lv_timer_t* upgrade_finish_timer_=nullptr;
    std::string upgrade_target_;
    void SetUpgradeProgress(const char*,const char*,int);
};
METHOD
int main(){
    LcdDisplay d;
    d.SetUpgradeProgress("STM32","queued",0);
    assert(d.upgrade_title_->text=="STM32 升级中");
    assert(d.upgrade_hint_->text=="请勿断电");
    d.SetUpgradeProgress("STM32","transferring",67);
    assert(d.upgrade_detail_->text=="传输固件  67%");
    assert(d.upgrade_progress_->value==67 && timers==0);
    d.SetUpgradeProgress("STM32","completed",100);
    assert(d.upgrade_title_->text=="STM32 升级完成" && timers==1);
    assert(d.upgrade_detail_->text=="固件已更新");
    d.SetUpgradeProgress("ESP32","queued",0);
    assert(timers==0 && d.upgrade_title_->text=="ESP32 升级中");
    d.SetUpgradeProgress("STM32","failed",0);
    assert(d.upgrade_title_->text=="ESP32 升级中");
    d.SetUpgradeProgress("ESP32","downloading",120);
    assert(d.upgrade_progress_->value==100);
    d.SetUpgradeProgress("ESP32","completed",100);
    assert(d.upgrade_detail_->text=="固件已更新" && timers==1);
    assert(d.upgrade_hint_->text.empty());
    d.SetUpgradeProgress("ESP32","queued",0);
    assert(timers==0);
    d.SetUpgradeProgress("ESP32","failed",0);
    assert(d.upgrade_title_->text=="ESP32 升级未完成" && timers==1);
    auto timer=d.upgrade_finish_timer_;timer->callback(timer);
    assert(timers==0 && d.upgrade_panel_->hidden && d.upgrade_target_.empty());
    d.SetUpgradeProgress("STM32","queued",0);
    assert(!d.upgrade_panel_->hidden);
    d.SetUpgradeProgress("STM32","failed",0);
    d.SetUpgradeProgress("STM32","queued",0);
    assert(timers==0 && !d.upgrade_panel_->hidden);
}
'''.replace('METHOD', method)
        compiler = os.environ.get('CXX') or shutil.which('g++')
        self.assertTrue(compiler)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'test.cc').write_text(program, encoding='utf-8')
            subprocess.run([compiler, '-std=c++14', '-finput-charset=UTF-8',
                            '-fexec-charset=UTF-8', str(path / 'test.cc'),
                            '-o', str(path / 'test.exe')], check=True, capture_output=True)
            env = dict(os.environ)
            env['PATH'] = str(Path(compiler).parent) + os.pathsep + env.get('PATH', '')
            subprocess.run([str(path / 'test.exe')], env=env, check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
