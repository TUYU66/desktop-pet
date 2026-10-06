package com.xiaozhi.modules.device.dao;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.xiaozhi.modules.device.entity.DeviceEntity;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface DeviceDao extends BaseMapper<DeviceEntity> {
}
