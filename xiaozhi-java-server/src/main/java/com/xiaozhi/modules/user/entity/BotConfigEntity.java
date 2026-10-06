package com.xiaozhi.modules.user.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.time.LocalDateTime;

@Data
@TableName("bot_config")
public class BotConfigEntity {
    private Long id;
    private Long userId;
    private String configKey;
    private String configValue;
    private LocalDateTime createDate;
    private LocalDateTime updateDate;
}
