package com.xiaozhi.common.exception;

import lombok.Getter;

@Getter
public enum ErrorCode {

    SUCCESS(0, "success"),
    INTERNAL_ERROR(500, "服务器内部错误"),
    UNAUTHORIZED(401, "未登录或 token 已过期"),
    FORBIDDEN(403, "无权限访问"),
    NOT_FOUND(404, "资源不存在"),
    BAD_REQUEST(400, "请求参数错误"),

    USER_NOT_FOUND(1001, "用户不存在"),
    PASSWORD_ERROR(1002, "密码错误"),
    USERNAME_EXISTS(1003, "用户名已存在"),
    ACCOUNT_INITIALIZED(1004, "账户已完成初始化，请使用已有账户登录"),
    ACCOUNT_CONFLICT(1005, "检测到多个账户，单用户配置异常，请联系维护者确认"),

    DEVICE_NOT_FOUND(2001, "设备未注册"),
    DEVICE_OFFLINE(2002, "设备不在线"),

    FIRMWARE_NOT_FOUND(4001, "固件不存在");

    private final int code;
    private final String msg;

    ErrorCode(int code, String msg) {
        this.code = code;
        this.msg = msg;
    }
}
