package com.xiaozhi.modules.user.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.xiaozhi.common.base.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
@TableName("sys_user")
public class UserEntity extends BaseEntity {

    private String username;
    private String password;
    private String email;
    private String mobile;
    private String avatar;
    private String nickname;
    private Integer status;
    private Integer superAdmin;
}
