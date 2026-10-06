#pragma once

#include <string>

enum class ManagementServiceState { Unknown, Checking, Online, Offline };

// Conversation state (idle/listening/speaking) and dependency readiness are
// separate: an idle device can still be disconnected or only partly usable.
struct DeviceReadiness {
    bool network = false;
    bool voice_service = false;
    bool connecting = false;
    ManagementServiceState management = ManagementServiceState::Unknown;
    bool chassis = false;
    bool chassis_seen = false;

    bool Ready() const {
        return network && voice_service && management == ManagementServiceState::Online && chassis;
    }

    std::string UnavailableStatus() const {
        std::string result;
        auto append = [&result](const char* text) {
            if (!result.empty()) result += " / ";
            result += text;
        };
        if (!network) append("网络未连接");
        else if (!voice_service) append(connecting ? "语音服务连接中" : "语音服务未连接");
        else if (management == ManagementServiceState::Offline) append("管理服务未连接");
        else if (management != ManagementServiceState::Online) append("管理服务状态待确认");
        if (!chassis) append(chassis_seen ? "底盘已断开" : "等待底盘连接");
        return result;
    }
};
