package com.xiaozhi.modules.device.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.xiaozhi.modules.device.dao.DeviceDao;
import com.xiaozhi.modules.device.entity.DeviceEntity;
import com.xiaozhi.modules.device.service.DeviceService;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

@Service
public class DeviceServiceImpl extends ServiceImpl<DeviceDao, DeviceEntity> implements DeviceService {

    @Override
    public DeviceEntity findByMac(String macAddress) {
        return this.getOne(new LambdaQueryWrapper<DeviceEntity>()
                .eq(DeviceEntity::getMacAddress, macAddress));
    }

    @Override
    public DeviceEntity getDeviceInfo() {
        // 单设备模式：返回第一个设备
        List<DeviceEntity> devices = this.list(
                new LambdaQueryWrapper<DeviceEntity>().orderByDesc(DeviceEntity::getUpdateDate));
        return devices.isEmpty() ? null : devices.get(0);
    }

    @Override
    public void updateStatus(Long deviceId, String status) {
        DeviceEntity device = new DeviceEntity();
        device.setId(deviceId);
        device.setStatus(status);
        if ("online".equals(status)) {
            device.setLastOnlineTime(LocalDateTime.now());
        }
        this.updateById(device);
    }

    @Override
    public void updateBattery(Long deviceId, Integer battery) {
        DeviceEntity device = new DeviceEntity();
        device.setId(deviceId);
        device.setBattery(battery);
        this.updateById(device);
    }
}
