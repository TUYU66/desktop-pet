package com.xiaozhi.modules.chat.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("agent_chat_history")
public class ChatHistoryEntity {

    private Long id;
    private Long agentId;
    private Long deviceId;
    private String sessionId;
    private String chatType;    // user/assistant/system
    private String content;
    private byte[] audioData;
    private String audioFormat;
    private Integer durationMs;
    private LocalDateTime reportTime;
    private LocalDateTime createDate;
}
