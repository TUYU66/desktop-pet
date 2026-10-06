package com.xiaozhi.modules.chat.dao;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.xiaozhi.modules.chat.entity.ChatHistoryEntity;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface ChatHistoryDao extends BaseMapper<ChatHistoryEntity> {
}
