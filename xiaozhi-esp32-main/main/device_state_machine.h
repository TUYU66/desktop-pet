#ifndef DEVICE_STATE_MACHINE_H
#define DEVICE_STATE_MACHINE_H

#include <atomic>
#include <functional>
#include <mutex>
#include <vector>

#include "device_state.h"

/**
 * 设备状态机：集中校验状态转换，并通过观察者回调通知其他模块。
 * 所有业务代码都应调用 TransitionTo()，不要直接改 current_state_。
 */
class DeviceStateMachine {
public:
    DeviceStateMachine();
    ~DeviceStateMachine() = default;

    // 状态机不可复制，避免监听器和当前状态出现多个副本。
    DeviceStateMachine(const DeviceStateMachine&) = delete;
    DeviceStateMachine& operator=(const DeviceStateMachine&) = delete;

    /** 原子读取当前设备状态。 */
    DeviceState GetState() const { return current_state_.load(); }

    /** 尝试切换到目标状态；非法路径返回 false，且不会通知监听器。 */
    bool TransitionTo(DeviceState new_state);

    /** 只检查当前状态能否切到 target，不产生副作用。 */
    bool CanTransitionTo(DeviceState target) const;

    /** 状态监听器参数依次是旧状态和新状态。 */
    using StateCallback = std::function<void(DeviceState, DeviceState)>;

    /** 注册状态监听器；回调在 TransitionTo() 调用者所在任务执行，返回值用于注销。 */
    int AddStateChangeListener(StateCallback callback);

    /** 按注册 ID 移除监听器。 */
    void RemoveStateChangeListener(int listener_id);

    /** 将枚举转换为日志使用的英文名称。 */
    static const char* GetStateName(DeviceState state);

private:
    std::atomic<DeviceState> current_state_{kDeviceStateUnknown};
    std::vector<std::pair<int, StateCallback>> listeners_;
    int next_listener_id_{0};
    std::mutex mutex_;

    /** 校验指定起点到终点的转换是否合法。 */
    bool IsValidTransition(DeviceState from, DeviceState to) const;

    /** 复制监听器列表后再回调，避免持锁执行外部代码造成死锁。 */
    void NotifyStateChange(DeviceState old_state, DeviceState new_state);
};

#endif // DEVICE_STATE_MACHINE_H
