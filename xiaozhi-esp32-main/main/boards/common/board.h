#ifndef BOARD_H
#define BOARD_H

#include <http.h>
#include <web_socket.h>
#include <mqtt.h>
#include <udp.h>
#include <string>
#include <functional>
#include <network_interface.h>

#include "led/led.h"
#include "backlight.h"
#include "assets.h"

/** 统一网络事件：Wi-Fi 和蜂窝板型都通过同一回调通知 Application。 */
enum class NetworkEvent {
    Scanning,              // 正在扫描网络。
    Connecting,            // 正在连接，data 为 SSID 或网络名称。
    Connected,             // 已连接，data 为 SSID 或网络名称。
    Disconnected,          // 网络断开。
    WifiConfigModeEnter,   // 进入 Wi-Fi 配网模式。
    WifiConfigModeExit,    // 退出 Wi-Fi 配网模式。
    ModemDetecting,        // 以下为蜂窝模块专用事件：正在探测模块。
    ModemErrorNoSim,       // 未检测到 SIM 卡。
    ModemErrorRegDenied,   // 运营商拒绝注册。
    ModemErrorInitFailed,  // 模块初始化失败。
    ModemErrorTimeout      // 操作超时。
};

// 板级实现把统一档位映射为 CPU 频率、Wi-Fi 省电和外设电源策略。
enum class PowerSaveLevel {
    LOW_POWER,    // 最大节能，适合待命。
    BALANCED,     // 功耗和响应速度折中。
    PERFORMANCE,  // 关闭节能，适合联网、音频和升级。
};

// data 携带 SSID、运营商等事件附加信息。
using NetworkEventCallback = std::function<void(NetworkEvent event, const std::string& data)>;

void* create_board();
class AudioCodec;
class Display;
class Board {
private:
    Board(const Board&) = delete; // 禁用拷贝构造函数
    Board& operator=(const Board&) = delete; // 禁用赋值操作

protected:
    Board();
    std::string GenerateUuid();

    // 软件生成的设备唯一标识
    std::string uuid_;

public:
    // create_board 由当前板型的 DECLARE_BOARD 生成，因此上层无需知道具体开发板类。
    static Board& GetInstance() {
        static Board* instance = static_cast<Board*>(create_board());
        return *instance;
    }

    virtual ~Board() = default;
    virtual std::string GetBoardType() = 0;
    virtual std::string GetUuid() { return uuid_; }
    virtual Backlight* GetBacklight() { return nullptr; }
    virtual Led* GetLed();
    virtual AudioCodec* GetAudioCodec() = 0;
    virtual bool GetTemperature(float& esp32temp);
    virtual Display* GetDisplay();
    virtual NetworkInterface* GetNetwork() = 0;
    virtual void StartNetwork() = 0;
    virtual void SetNetworkEventCallback(NetworkEventCallback callback) { (void)callback; }
    virtual const char* GetNetworkStateIcon() = 0;
    virtual bool GetBatteryLevel(int &level, bool& charging, bool& discharging);
    // Optional external protection flag; unsupported boards retain their
    // existing charge-controller based low-battery behavior.
    virtual bool UsesBatteryProtectionFlag() const { return false; }
    virtual bool GetLowBatteryFlag(bool& low) { (void)low; return false; }
    virtual bool IsChassisConnected() const { return true; } // Boards without a chassis need no UART readiness gate.
    virtual std::string GetMotionHint(bool& busy) { busy = false; return ""; }
    virtual std::string GetSystemInfoJson();
    virtual void SetPowerSaveLevel(PowerSaveLevel level) = 0;
    virtual std::string GetBoardJson() = 0;
    virtual std::string GetDeviceStatusJson() = 0;
};

#define DECLARE_BOARD(BOARD_CLASS_NAME) \
void* create_board() { \
    return new BOARD_CLASS_NAME(); \
}

#endif // BOARD_H
