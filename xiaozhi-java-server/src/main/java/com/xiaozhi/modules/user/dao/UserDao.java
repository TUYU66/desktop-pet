package com.xiaozhi.modules.user.dao;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface UserDao extends BaseMapper<UserEntity> {
}
