package com.xiaozhi.modules.device.service;

import com.xiaozhi.modules.device.entity.DeviceEntity;

public interface DeviceService {

    DeviceEntity findByMac(String macAddress);

    DeviceEntity getDeviceInfo();

    void updateStatus(Long deviceId, String status);

    void updateBattery(Long deviceId, Integer battery);
}
