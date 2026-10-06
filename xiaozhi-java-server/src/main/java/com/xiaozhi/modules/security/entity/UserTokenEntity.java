package com.xiaozhi.modules.security.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("sys_user_token")
public class UserTokenEntity {

    private Long id;
    private Long userId;
    private String token;
    private LocalDateTime expireDate;
    private LocalDateTime createDate;
    private LocalDateTime updateDate;
}
