#include <esp_log.h>
#include <esp_err.h>
#include <string>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <font_awesome.h>

#include "lvgl_display.h"
#include "board.h"
#include "application.h"
#include "audio_codec.h"
#include "settings.h"
#include "assets/lang_config.h"

#define TAG "Display"

LvglDisplay::LvglDisplay() {
    // Notification timer
    esp_timer_create_args_t notification_timer_args = {
        .callback = [](void *arg) {
            LvglDisplay *display = static_cast<LvglDisplay*>(arg);
            DisplayLockGuard lock(display);
            lv_obj_add_flag(display->notification_label_, LV_OBJ_FLAG_HIDDEN);
            lv_obj_remove_flag(display->status_label_, LV_OBJ_FLAG_HIDDEN);
        },
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "notification_timer",
        .skip_unhandled_events = false,
    };
    ESP_ERROR_CHECK(esp_timer_create(&notification_timer_args, &notification_timer_));

    // Create a power management lock
    auto ret = esp_pm_lock_create(ESP_PM_APB_FREQ_MAX, 0, "display_update", &pm_lock_);
    if (ret == ESP_ERR_NOT_SUPPORTED) {
        ESP_LOGI(TAG, "Power management not supported");
    } else {
        ESP_ERROR_CHECK(ret);
    }
}

LvglDisplay::~LvglDisplay() {
    if (notification_timer_ != nullptr) {
        esp_timer_stop(notification_timer_);
        esp_timer_delete(notification_timer_);
    }

    if (network_label_ != nullptr) {
        lv_obj_del(network_label_);
    }
    if (notification_label_ != nullptr) {
        lv_obj_del(notification_label_);
    }
    if (status_label_ != nullptr) {
        lv_obj_del(status_label_);
    }
    if (mute_label_ != nullptr) {
        lv_obj_del(mute_label_);
    }
    if (battery_label_ != nullptr) {
        lv_obj_del(battery_label_);
    }
    if( low_battery_popup_ != nullptr ) {
        lv_obj_del(low_battery_popup_);
    }
    if (pm_lock_ != nullptr) {
        esp_pm_lock_delete(pm_lock_);
    }
}

void LvglDisplay::SetStatus(const char* status) {
    if (!setup_ui_called_) {
        ESP_LOGW(TAG, "SetStatus('%s') called before SetupUI() - message will be lost!", status);
    }
    DisplayLockGuard lock(this);
    if (status_label_ == nullptr) {
        if (setup_ui_called_) {
            ESP_LOGW(TAG, "SetStatus('%s') failed: status_label_ is nullptr (SetupUI() was called but label not created)", status);
        }
        return;
    }
    base_status_ = status;
    if (Application::GetInstance().GetDeviceState() != kDeviceStateIdle || !Application::GetInstance().IsSystemReady()) motion_hint_.clear();
    lv_label_set_text(status_label_, motion_hint_.empty() ? status : motion_hint_.c_str());
    lv_obj_remove_flag(status_label_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(notification_label_, LV_OBJ_FLAG_HIDDEN);

    last_status_update_time_ = std::chrono::system_clock::now();
}

void LvglDisplay::ShowNotification(const std::string &notification, int duration_ms) {
    ShowNotification(notification.c_str(), duration_ms);
}

void LvglDisplay::ShowNotification(const char* notification, int duration_ms) {
    if (!setup_ui_called_) {
        ESP_LOGW(TAG, "ShowNotification('%s') called before SetupUI() - message will be lost!", notification);
    }
    DisplayLockGuard lock(this);
    if (notification_label_ == nullptr) {
        if (setup_ui_called_) {
            ESP_LOGW(TAG, "ShowNotification('%s') failed: notification_label_ is nullptr (SetupUI() was called but label not created)", notification);
        }
        return;
    }
    lv_label_set_text(notification_label_, notification);
    lv_obj_remove_flag(notification_label_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(status_label_, LV_OBJ_FLAG_HIDDEN);

    esp_timer_stop(notification_timer_);
    ESP_ERROR_CHECK(esp_timer_start_once(notification_timer_, duration_ms * 1000));
}

void LvglDisplay::UpdateStatusBar(bool update_all) {
    auto& app = Application::GetInstance();
    auto& board = Board::GetInstance();
    auto codec = board.GetAudioCodec();
    bool motion_busy = false;
    const auto motion_hint = board.GetMotionHint(motion_busy);
    {
        // UpdateStatusBar runs on the Application main task. UART/worker
        // tasks only publish snapshots; they never touch LVGL objects.
        DisplayLockGuard lock(this);
        const auto hint = app.GetDeviceState() == kDeviceStateIdle && app.IsSystemReady() ? motion_hint : std::string();
        if (motion_hint_ != hint) {
            motion_hint_ = hint;
            if (status_label_) lv_label_set_text(status_label_,
                motion_hint_.empty() ? base_status_.c_str() : motion_hint_.c_str());
        }
    }

    // Update mute icon
    {
        DisplayLockGuard lock(this);
        if (mute_label_ == nullptr) {
            return;
        }

        // Update icon if mute state changes
        if (codec->output_volume() == 0 && !muted_) {
            muted_ = true;
            lv_label_set_text(mute_label_, FONT_AWESOME_VOLUME_XMARK);
        } else if (codec->output_volume() > 0 && muted_) {
            muted_ = false;
            lv_label_set_text(mute_label_, "");
        }
    }

    // Update time
    if (app.GetDeviceState() == kDeviceStateIdle && app.IsSystemReady() && motion_hint.empty()) {
        if (last_status_update_time_ + std::chrono::seconds(10) < std::chrono::system_clock::now()) {
            // Set status to clock "HH:MM"
            time_t now = time(NULL);
            struct tm* tm = localtime(&now);
            // Check if the we have already set the time
            if (tm->tm_year >= 2025 - 1900) {
                char time_str[16];
                strftime(time_str, sizeof(time_str), "%H:%M", tm);
                // Clock text is not a device-state transition. Bypass derived
                // SetStatus handlers so idle animations keep running.
                LvglDisplay::SetStatus(time_str);
            } else {
                ESP_LOGW(TAG, "System time is not set, tm_year: %d", tm->tm_year);
            }
        }
    }

    esp_pm_lock_acquire(pm_lock_);
    // Update battery icon
    int battery_level = 0;
    bool charging = false, discharging = false;
    const char* icon = nullptr;
    const bool battery_valid = board.GetBatteryLevel(battery_level, charging, discharging);
    if (battery_valid) {
        if (charging) {
            icon = FONT_AWESOME_BATTERY_BOLT;
        } else {
            const char* levels[] = {
                FONT_AWESOME_BATTERY_EMPTY, // 0-19%
                FONT_AWESOME_BATTERY_QUARTER,    // 20-39%
                FONT_AWESOME_BATTERY_HALF,    // 40-59%
                FONT_AWESOME_BATTERY_THREE_QUARTERS,    // 60-79%
                FONT_AWESOME_BATTERY_FULL, // 80-99%
                FONT_AWESOME_BATTERY_FULL, // 100%
            };
            icon = levels[std::max(0, std::min(100, battery_level)) / 20];
        }
        DisplayLockGuard lock(this);
        if (battery_label_ != nullptr && battery_icon_ != icon) {
            battery_icon_ = icon;
            lv_label_set_text(battery_label_, battery_icon_);
        }

        // Check low battery popup only when clock tick event is triggered
        // Because when initializing, the battery level is not ready yet.
        if (low_battery_popup_ != nullptr && !update_all && !board.UsesBatteryProtectionFlag()) {
            if (strcmp(icon, FONT_AWESOME_BATTERY_EMPTY) == 0 && discharging) {
                if (lv_obj_has_flag(low_battery_popup_, LV_OBJ_FLAG_HIDDEN)) { // Show if low battery popup is hidden
                    lv_obj_remove_flag(low_battery_popup_, LV_OBJ_FLAG_HIDDEN);
                    app.Schedule([&app]() {
                        app.PlaySound(Lang::Sounds::OGG_LOW_BATTERY);
                    });
                }
            } else {
                // Hide the low battery popup when the battery is not empty
                if (!lv_obj_has_flag(low_battery_popup_, LV_OBJ_FLAG_HIDDEN)) { // Hide if low battery popup is shown
                    lv_obj_add_flag(low_battery_popup_, LV_OBJ_FLAG_HIDDEN);
                }
            }
        }
    } else {
        DisplayLockGuard lock(this);
        battery_icon_ = nullptr;
        if (battery_label_) lv_label_set_text(battery_label_, "");
        if (low_battery_popup_) lv_obj_add_flag(low_battery_popup_, LV_OBJ_FLAG_HIDDEN);
    }

    if (board.UsesBatteryProtectionFlag()) {
        bool low = false;
        const bool flag_valid = battery_valid && board.GetLowBatteryFlag(low);
        const int64_t now = esp_timer_get_time();
        bool show = false;
        if (!flag_valid) {
            // Losing telemetry is not recovery: retain the once-per-episode
            // latch, but require a fresh continuous window on return.
            low_since_us_ = recovered_since_us_ = low_popup_until_us_ = 0;
        } else if (!low) {
            low_since_us_ = low_popup_until_us_ = 0;
            if (!recovered_since_us_) recovered_since_us_ = now;
            if (now - recovered_since_us_ >= 10000000) low_notified_ = false;
        } else {
            recovered_since_us_ = 0;
            if (!low_since_us_) low_since_us_ = now;
            const bool quiet = app.GetDeviceState() == kDeviceStateIdle &&
                !motion_busy && motion_hint.empty() && app.GetAudioService().WaitForPlaybackQueueEmpty(0);
            if (!update_all && !low_notified_ && !low_reminder_queued_ && now - low_since_us_ >= 3000000 && quiet) {
                low_reminder_queued_ = true;
                const int64_t episode = low_since_us_;
                app.Schedule([this, episode]() {
                    low_reminder_queued_ = false;
                    auto& app = Application::GetInstance();
                    auto& board = Board::GetInstance();
                    bool low = false, busy = false;
                    const auto hint = board.GetMotionHint(busy);
                    if (!low_notified_ && low_since_us_ == episode &&
                        board.GetLowBatteryFlag(low) && low && !busy && hint.empty() &&
                        app.GetDeviceState() == kDeviceStateIdle && app.GetAudioService().WaitForPlaybackQueueEmpty(0)) {
                        app.PlaySound(Lang::Sounds::OGG_LOW_BATTERY);
                        low_notified_ = true;
                        low_popup_until_us_ = esp_timer_get_time() + 4000000;
                    }
                });
            }
            show = quiet && low_popup_until_us_ > now;
        }
        DisplayLockGuard lock(this);
        if (low_battery_popup_) {
            if (show) lv_obj_remove_flag(low_battery_popup_, LV_OBJ_FLAG_HIDDEN);
            else lv_obj_add_flag(low_battery_popup_, LV_OBJ_FLAG_HIDDEN);
        }
    }

    // Update network icon every 10 seconds
    static int seconds_counter = 0;
    if (update_all || seconds_counter++ % 10 == 0) {
        // Don't read 4G network status during firmware upgrade to avoid occupying UART resources
        auto device_state = Application::GetInstance().GetDeviceState();
        static const std::vector<DeviceState> allowed_states = {
            kDeviceStateIdle,
            kDeviceStateStarting,
            kDeviceStateWifiConfiguring,
            kDeviceStateListening,
            kDeviceStateActivating,
        };
        if (std::find(allowed_states.begin(), allowed_states.end(), device_state) != allowed_states.end()) {
            icon = board.GetNetworkStateIcon();
            if (network_label_ != nullptr && icon != nullptr && network_icon_ != icon) {
                DisplayLockGuard lock(this);
                network_icon_ = icon;
                lv_label_set_text(network_label_, network_icon_);
            }
        }
    }

    esp_pm_lock_release(pm_lock_);
}

void LvglDisplay::SetPreviewImage(std::unique_ptr<LvglImage> image) {
}

void LvglDisplay::SetPowerSaveMode(bool on) {
    if (on) {
        SetChatMessage("system", "");
        SetEmotion("sleepy");
    } else {
        SetChatMessage("system", "");
        SetEmotion("neutral");
    }
}
