package com.xiaozhi.modules.memory.dao;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.xiaozhi.modules.memory.entity.UserMemoryEntity;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface UserMemoryDao extends BaseMapper<UserMemoryEntity> {
}
