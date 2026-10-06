package com.xiaozhi.modules.ota.dao;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.xiaozhi.modules.ota.entity.FirmwareEntity;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface FirmwareDao extends BaseMapper<FirmwareEntity> {
}
