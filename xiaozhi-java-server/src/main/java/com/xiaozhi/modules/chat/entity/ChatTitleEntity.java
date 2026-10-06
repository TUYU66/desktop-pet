package com.xiaozhi.modules.chat.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("agent_chat_title")
public class ChatTitleEntity {

    private Long id;
    private Long agentId;
    private String sessionId;
    private String title;
    private LocalDateTime createDate;
    private LocalDateTime updateDate;
    private String roleId;
    private String roleName;
    private String memorySummary;
}
