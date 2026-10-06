package com.xiaozhi.modules.security.dao;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.xiaozhi.modules.security.entity.UserTokenEntity;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface UserTokenDao extends BaseMapper<UserTokenEntity> {
}
