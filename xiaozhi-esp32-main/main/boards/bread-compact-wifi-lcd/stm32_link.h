#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/queue.h>
#include <string>
#include <mutex>
#include <array>
#include <deque>

// UART bridge to the car board's J13 (STM32 USART2).
class Stm32Link {
public:
    static Stm32Link& GetInstance();
    void Start();
    std::string GetStatusJson() const;
    std::string GetBatteryText() const;
    bool GetBatteryLevel(int& percent) const;
    bool GetLowBattery(bool& low) const;
    bool IsConnected() const { return LinkFresh(); }
    void OnBluetoothSpeechResult(unsigned int id, bool played);
    int GetBluetoothControlState() const; // -1=unknown, 0=off, 1=on; fresh STM32 feedback only.
    std::string GetMotionHint(bool& busy) const;
    std::string GetCalibrationJson() const;
    std::string RequestCalibration(const std::string& operation, int wheel, int pwm, bool confirmed, unsigned int expected_session, unsigned int sample_tick);
    std::string GetTuningJson(int after = 0, int epoch = 0) const;
    std::string RequestTuning(const std::string& operation, unsigned int revision, unsigned int sample_tick,
                              const std::string& values);
    std::string RequestStand();
    std::string RequestBluetooth(bool enabled);
    std::string RequestRest();
    std::string RequestTurnLeft();
    std::string RequestTurnRight();
    std::string RequestTurnAround();
    std::string StartOta(const std::string& job, const std::string& url, const std::string& metadata);
    std::string GetOtaStateJson() const;

private:
    static void TaskEntry(void* context);
    void Poll();
    void ProcessLine(const char* line);
    void FeedByte(uint8_t byte);
    std::string RequestMotion(const char* command, unsigned int app_request = 0, bool for_update = false);
    struct OtaWork;
    static void OtaTask(void* context);
    void RunOta(const OtaWork& work);
    void OtaProgress(const char* phase, int progress, const std::string& error = "");
    std::atomic<bool> ota_running_{false}, ota_exclusive_{false};
    std::atomic<bool> ota_supported_{false};
    std::atomic<int> boot_recovery_state_{-1};
    std::atomic<TickType_t> boot_recovery_tick_{0};
    int BootRecoveryState() const;
    mutable std::mutex ota_mutex_;
    std::string ota_job_, ota_phase_{"idle"}, ota_error_, ota_version_;
    int ota_progress_{0};
    std::atomic<uint32_t> installed_ota_version_{0};
    std::atomic<TickType_t> installed_ota_tick_{0};
    struct AppBluetoothRequest { unsigned int id; bool enabled; };
    static void AppBluetoothTask(void* context);
    void RunAppBluetooth(const AppBluetoothRequest& request);
    bool AnnounceAppBluetooth(const AppBluetoothRequest& request, bool success);
    void SendAppBluetoothResult(unsigned int id, bool success);
    QueueHandle_t app_bluetooth_queue_{nullptr};
    std::atomic<unsigned int> app_bluetooth_id_{0};
    std::atomic<bool> app_bluetooth_target_{false};
    bool BatteryFresh() const;
    bool LinkFresh() const;
    void MarkFrame(); // Only after CRC + recognized payload validation.
    void PublishReply(uint16_t sequence, int kind, int reply);
    void SetAction(const char* action, const char* phase);
    void FinishAction(const char* result);
    struct MotionSnapshot {
        std::string action;
        std::string phase;
        std::string result;
        bool busy;
        bool valid;
    };
    MotionSnapshot GetMotionSnapshot() const;

    std::atomic<bool> started_{false};
    std::atomic<bool> connected_{false};
    std::atomic<bool> status_valid_{false};
    std::atomic<int> battery_mv_{-1};
    std::atomic<int> balance_stopped_{1};
    std::atomic<int> low_battery_{0};
    std::atomic<uint16_t> next_sequence_{1};
    std::atomic<uint16_t> pending_sequence_{0};
    std::atomic<int> pending_reply_{0};
    std::atomic<int> pending_kind_{0}; // 1=posture, 2=turn, 3=BT with stand/rest
    std::atomic<int> bluetooth_mode_{-1};
    std::atomic<TickType_t> bluetooth_tick_{0};
    std::atomic<bool> request_busy_{false};
    std::atomic<bool> calibration_supported_{false}, calibration_active_{false};
    std::atomic<unsigned int> calibration_session_{0};
    std::atomic<TickType_t> calibration_tick_{0};
    std::atomic<uint16_t> calibration_pending_{0};
    std::atomic<int> calibration_reply_{-1};
    SemaphoreHandle_t calibration_semaphore_{nullptr};
    mutable std::mutex calibration_mutex_;
    int64_t calibration_fields_[12]{};
    mutable std::mutex tuning_mutex_;
    int64_t tuning_fields_[12]{};
    int64_t tuning_ack_fields_[12]{};
    TickType_t tuning_tick_{0}, live_tick_{0};
    bool tuning_seen_{false}, live_seen_{false};
    int64_t gyro_fields_[13]{};
    TickType_t gyro_tick_{0};
    bool gyro_seen_{false};
    int tuning_epoch_{1}, live_cursor_{0};
    struct LivePoint { int cursor; std::array<int64_t,19> fields; };
    std::deque<LivePoint> live_points_;
    std::atomic<uint16_t> tuning_pending_{0};
    SemaphoreHandle_t tuning_semaphore_{nullptr};
    mutable std::mutex motion_mutex_;
    std::string active_action_;
    std::string request_phase_;
    std::string last_result_{"unknown"};
    TickType_t result_tick_{0};
    TickType_t motion_tick_{0};
    int motion_phase_{-1};
    int motion_error_{0};
    int motion_rest_{0};
    int posture_state_{0};
    bool posture_seen_{false};
    TickType_t posture_tick_{0};
    bool motion_stopped_{true}; // DIAG,M sample; protected by motion_mutex_.
    bool motion_seen_{false};
    SemaphoreHandle_t uart_mutex_{nullptr};
    SemaphoreHandle_t request_mutex_{nullptr};
    SemaphoreHandle_t reply_semaphore_{nullptr};
    QueueHandle_t uart_events_{nullptr};
    std::atomic<TickType_t> last_frame_tick_{0};
    std::atomic<TickType_t> last_battery_tick_{0};
    TickType_t last_ping_tick_{0};
    TickType_t last_link_report_tick_{0};
    uint32_t crc_bad_{0}, framing_bad_{0}, fifo_overflow_{0}, buffer_full_{0};
    uint32_t uart_frame_error_{0}, parity_error_{0}, resync_{0};
    char rx_line_[256]{};
    size_t rx_length_{0};
    bool rx_overflow_{true}; // Ignore bytes until the next v2 frame marker.
};
