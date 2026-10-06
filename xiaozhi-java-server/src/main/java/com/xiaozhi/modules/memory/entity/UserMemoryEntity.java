package com.xiaozhi.modules.memory.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.FieldStrategy;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("user_memory")
public class UserMemoryEntity {
    private Long id;
    private Long userId;
    private String roleId;
    private String category;
    private String content;
    private String sourceType;
    private String sourceSessionId;
    @TableField(updateStrategy = FieldStrategy.ALWAYS) private String factJson;
    @TableField(updateStrategy = FieldStrategy.ALWAYS) private String factKey;
    @TableField(updateStrategy = FieldStrategy.ALWAYS) private String slotKey;
    @TableField(updateStrategy = FieldStrategy.ALWAYS) private String contentHash;
    private String sourceTurnId;
    private Integer version;
    private LocalDateTime createDate;
    private LocalDateTime updateDate;
}
