package com.xiaozhi.modules.device.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.xiaozhi.common.base.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

@Data
@EqualsAndHashCode(callSuper = true)
@TableName("device")
public class DeviceEntity extends BaseEntity {

    private String macAddress;
    private String uuid;
    private String deviceModel;
    private String deviceVersion;
    private String status;          // online / offline
    private Integer battery;        // 电量百分比 0-100
    private LocalDateTime lastOnlineTime;
    private String extraConfig;
}
