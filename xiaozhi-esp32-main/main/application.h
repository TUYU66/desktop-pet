#ifndef _APPLICATION_H_
#define _APPLICATION_H_

#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <freertos/task.h>
#include <esp_timer.h>

#include <string>
#include <mutex>
#include <deque>
#include <memory>
#include <atomic>

#include "protocol.h"
#include "ota.h"
#include "audio_service.h"
#include "device_state.h"
#include "device_state_machine.h"

// 主事件组位：其他任务只负责置位，真正的状态切换和业务处理集中到主任务中完成。
#define MAIN_EVENT_SCHEDULE             (1 << 0)
#define MAIN_EVENT_SEND_AUDIO           (1 << 1)
#define MAIN_EVENT_WAKE_WORD_DETECTED   (1 << 2)
#define MAIN_EVENT_VAD_CHANGE           (1 << 3)
#define MAIN_EVENT_ERROR                (1 << 4)
#define MAIN_EVENT_ACTIVATION_DONE      (1 << 5)
#define MAIN_EVENT_CLOCK_TICK           (1 << 6)
#define MAIN_EVENT_NETWORK_CONNECTED    (1 << 7)
#define MAIN_EVENT_NETWORK_DISCONNECTED (1 << 8)
#define MAIN_EVENT_TOGGLE_CHAT          (1 << 9)
#define MAIN_EVENT_START_LISTENING      (1 << 10)
#define MAIN_EVENT_STOP_LISTENING       (1 << 11)
#define MAIN_EVENT_STATE_CHANGED        (1 << 12)


enum AecMode {
    kAecOff,           // 不启用回声消除
    kAecOnDeviceSide,  // 设备端 AFE 消除扬声器回声
    kAecOnServerSide,  // 上传时间戳，由服务端完成回声消除
};

class Application {
public:
    static Application& GetInstance() {
        static Application instance;
        return instance;
    }
    // 单例不允许复制，避免出现两套状态机、音频服务和网络协议实例。
    Application(const Application&) = delete;
    Application& operator=(const Application&) = delete;

    /** 初始化显示、音频、网络回调和定时器；联网过程异步进行。 */
    void Initialize();

    /** 主事件循环：统一处理网络、状态变化和用户交互，正常情况下不会返回。 */
    void Run();

    DeviceState GetDeviceState() const { return state_machine_.GetState(); }
    bool IsConversationAwake() const { return conversation_awake_.load(); }
    bool IsSystemReady() const;
    bool IsVoiceDetected() const { return audio_service_.IsVoiceDetected(); }
    
    /** 请求状态切换；只有状态机允许该路径时才返回 true。 */
    bool SetDeviceState(DeviceState state);

    /** 把任意任务中的回调投递到主任务执行，用于避免跨任务直接修改 UI/状态。 */
    void Schedule(std::function<void()>&& callback);

    /** 显示提示状态、正文和表情，并可选播放提示音。 */
    void Alert(const char* status, const char* message, const char* emotion = "", const std::string_view& sound = "");
    void DismissAlert();

    void AbortSpeaking(AbortReason reason);

    /** 线程安全地请求切换对话状态，实际逻辑由 Run() 处理。 */
    void ToggleChatState();

    /** 线程安全地请求开始监听。 */
    void StartListening();

    /** 线程安全地请求停止监听。 */
    void StopListening();

    void Reboot();
    void WakeWordInvoke(const std::string& wake_word);
    bool UpgradeFirmware(const std::string& url, const std::string& version = "");
    bool CanEnterSleepMode();
    void SendMcpMessage(const std::string& payload);
    void SendMcpMessage(const std::string& payload, uint32_t session);
    void SetAecMode(AecMode mode);
    AecMode GetAecMode() const { return aec_mode_; }
    void PlaySound(const std::string_view& sound);
    AudioService& GetAudioService() { return audio_service_; }
    
    /**
     * 线程安全地释放联网后创建的协议资源，包括音频通道、Protocol 和 OTA 对象。
     * 任何任务都可调用，但资源销毁会被安排在主任务中执行。
     */
    void ResetProtocol();

private:
    Application();
    ~Application();

    std::mutex mutex_;  // 保护 main_tasks_，不保护整个 Application。
    std::deque<std::function<void()>> main_tasks_;  // 等待主任务串行执行的回调。
    std::shared_ptr<Protocol> protocol_;
    EventGroupHandle_t event_group_ = nullptr;
    esp_timer_handle_t clock_timer_handle_ = nullptr;
    DeviceStateMachine state_machine_;
    ListeningMode listening_mode_ = kListeningModeAutoStop;
    AecMode aec_mode_ = kAecOff;
    std::atomic<bool> conversation_awake_{false}; // Only a wake-word event opens a voice session.
    bool speech_listen_allowed_ = false; // Main-task owned, independently authorized per stream.
    enum class ListeningDisplayPhase { Waiting, Recognizing, Thinking, Empty, Timeout, Error };
    ListeningDisplayPhase listening_display_phase_ = ListeningDisplayPhase::Waiting;
    bool listening_voice_active_ = false;
    int64_t listening_voice_hold_until_us_ = 0; // Brief pause grace; main-task owned.
    std::string last_error_message_;
    AudioService audio_service_;
    std::unique_ptr<Ota> ota_;

    bool has_server_time_ = false;  // OTA/握手响应是否提供过可信服务器时间。
    std::atomic<bool> network_connected_{false};  // 板级网络已就绪，不等同于 WebSocket 已连接。
    std::atomic<bool> aborted_{false};  // 网络接收任务也读取，用于丢弃被打断的旧音频。
    std::atomic<uint32_t> playback_epoch_{0};
    // Keep a confirmed speech onset until the main loop handles it, even if VAD
    // has already fallen back to silence. Epoch excludes onsets from old replies.
    std::atomic<uint32_t> speech_barge_in_epoch_{0};
    std::atomic<bool> speech_barge_in_pending_{false};
    bool tts_active_ = false;  // TTS 数据正在流式推送，用于音频接收判断
    bool music_playing_ = false;
    bool reminder_playing_ = false;
    bool reminder_tone_ = false;
    std::string audio_stream_id_;
    std::string last_standby_request_id_;
    bool tts_sentence_started_ = false;
    bool assets_version_checked_ = false;
    bool play_popup_on_listening_ = false;  // 进入监听态后是否播放提示音。
    bool connection_task_running_ = false; // Main-task owned; at most one handshake.
    uint32_t protocol_generation_ = 0;
    uint32_t maintenance_ticks_ = 0; // Never reset by conversation state changes.
    bool chassis_ever_connected_ = false;
    bool ready_announced_ = false;
    std::string availability_status_;
    std::string idle_status_;
    bool pending_wake_ = false;
    std::string pending_wake_word_;
    ListeningMode pending_listening_mode_ = kListeningModeAutoStop;
    int clock_ticks_ = 0;
    TaskHandle_t activation_task_handle_ = nullptr;


    // 以下处理器均由主事件循环调用，不应直接从网络/音频任务调用。
    DeviceReadiness GetReadiness() const;
    void UpdateAvailability(bool force = false);
    void StopUnavailableConversation();
    void UpdateSpeakingAudio();
    void UpdateListeningStatus();
    void StartConnectionTask();
    void HandleStateChangedEvent();
    void HandleToggleChatEvent();
    void HandleStartListeningEvent();
    void HandleStopListeningEvent();
    void HandleNetworkConnectedEvent();
    void HandleNetworkDisconnectedEvent();
    void HandleActivationDoneEvent();
    void HandleWakeWordDetectedEvent();
    void ContinueOpenAudioChannel(ListeningMode mode);
    void ContinueWakeWordInvoke(const std::string& wake_word);

    // 激活流程会访问网络，因此放在后台任务执行。
    void ActivationTask();

    // 初始化、升级及监听模式辅助函数。
    void CheckAssetsVersion();
    void CheckNewVersion();
    void InitializeProtocol();
    void ShowActivationCode(const std::string& code, const std::string& message);
    void SetListeningMode(ListeningMode mode);
    ListeningMode GetDefaultListeningMode() const;
    
    // 状态机监听器：把状态变化转成主事件，避免在回调栈内执行复杂业务。
    void OnStateChanged(DeviceState old_state, DeviceState new_state);
};


class TaskPriorityReset {
public:
    // RAII 临时调整当前 FreeRTOS 任务优先级，离开作用域时自动恢复。
    TaskPriorityReset(BaseType_t priority) {
        original_priority_ = uxTaskPriorityGet(NULL);
        vTaskPrioritySet(NULL, priority);
    }
    ~TaskPriorityReset() {
        vTaskPrioritySet(NULL, original_priority_);
    }

private:
    BaseType_t original_priority_;
};

#endif // _APPLICATION_H_
