#ifndef _OTA_H
#define _OTA_H

#include <functional>
#include <string>

#include <esp_err.h>
#include "board.h"

class Ota {
public:
    Ota();
    ~Ota();

    // 一次检查同时获取固件版本、协议配置、服务器时间和激活信息。
    esp_err_t CheckVersion();
    // 使用服务端 challenge 完成设备激活；202 表示仍需稍后轮询。
    esp_err_t Activate();
    bool HasActivationChallenge() { return has_activation_challenge_; }
    bool HasNewVersion() { return has_new_version_; }
    bool HasMqttConfig() { return has_mqtt_config_; }
    bool HasWebsocketConfig() { return has_websocket_config_; }
    bool HasActivationCode() { return has_activation_code_; }
    bool HasServerTime() { return has_server_time_; }
    // 下载并写入 OTA 分区。回调参数为百分比和当前下载速度（字节/秒）。
    bool StartUpgrade(std::function<void(int progress, size_t speed)> callback);
    static bool Upgrade(const std::string& firmware_url, std::function<void(int progress, size_t speed)> callback);
    void MarkCurrentVersionValid();

    const std::string& GetFirmwareVersion() const { return firmware_version_; }
    const std::string& GetCurrentVersion() const { return current_version_; }
    const std::string& GetFirmwareUrl() const { return firmware_url_; }
    const std::string& GetActivationMessage() const { return activation_message_; }
    const std::string& GetActivationCode() const { return activation_code_; }
    std::string GetCheckVersionUrl();

private:
    // 以下标志表示本次 CheckVersion 响应中是否包含对应区块，避免使用残缺配置。
    std::string activation_message_;
    std::string activation_code_;
    bool has_new_version_ = false;
    bool has_mqtt_config_ = false;
    bool has_websocket_config_ = false;
    bool has_server_time_ = false;
    bool has_activation_code_ = false;
    bool has_serial_number_ = false;
    bool has_activation_challenge_ = false;
    std::string current_version_;
    std::string firmware_version_;
    std::string firmware_url_;
    std::string activation_challenge_;
    std::string serial_number_;
    int activation_timeout_ms_ = 30000;  // 服务端建议的激活等待上限。

    std::function<void(int progress, size_t speed)> upgrade_callback_;
    // 将 1.2.3 拆成整数数组，供逐段版本比较使用。
    std::vector<int> ParseVersion(const std::string& version);
    bool IsNewVersionAvailable(const std::string& currentVersion, const std::string& newVersion);
    std::string GetActivationPayload();
    std::unique_ptr<Http> SetupHttp();
};

#endif // _OTA_H
