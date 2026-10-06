#include "../main/device_readiness.h"
#include <cassert>

int main() {
    DeviceReadiness status;
    assert(!status.Ready());
    assert(status.UnavailableStatus() == "网络未连接 / 等待底盘连接");
    status.network = true;
    status.connecting = true;
    assert(status.UnavailableStatus() == "语音服务连接中 / 等待底盘连接");
    status.voice_service = true;
    status.management = ManagementServiceState::Online;
    assert(!status.Ready()); // Python/Java online alone must not announce ready.
    assert(status.UnavailableStatus() == "等待底盘连接");
    status.chassis = true;
    status.chassis_seen = true;
    assert(status.Ready());
    assert(status.UnavailableStatus().empty());
    status.management = ManagementServiceState::Offline;
    assert(!status.Ready());
    assert(status.UnavailableStatus() == "管理服务未连接");
    status.management = ManagementServiceState::Unknown;
    assert(status.UnavailableStatus() == "管理服务状态待确认");
    status.voice_service = false;
    status.connecting = false;
    assert(status.UnavailableStatus() == "语音服务未连接");
    status.voice_service = true;
    status.management = ManagementServiceState::Online;
    status.chassis = false;
    assert(status.UnavailableStatus() == "底盘已断开");
    status.chassis = true;
    assert(status.Ready());
}
