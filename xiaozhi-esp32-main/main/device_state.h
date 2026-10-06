#ifndef _DEVICE_STATE_H_
#define _DEVICE_STATE_H_

enum DeviceState {
    kDeviceStateUnknown,          // 对象刚创建，尚未开始初始化。
    kDeviceStateStarting,         // 初始化显示、音频和网络组件。
    kDeviceStateWifiConfiguring,  // 等待用户配置 Wi-Fi。
    kDeviceStateIdle,             // 待命，可保留传输连接但不上传麦克风。
    kDeviceStateConnecting,       // 正在建立对话协议连接。
    kDeviceStateListening,        // 正在采集并上传用户语音。
    kDeviceStateSpeaking,         // 正在接收并播放助手语音。
    kDeviceStateUpgrading,        // 正在更新固件或资源。
    kDeviceStateActivating,       // 正在检查版本、激活并加载协议配置。
    kDeviceStateAudioTesting,     // 配网阶段的本地麦克风测试。
    kDeviceStateFatalError        // 不可恢复错误，只能重启。
};

#endif // _DEVICE_STATE_H_
