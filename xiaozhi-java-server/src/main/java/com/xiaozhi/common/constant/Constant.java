package com.xiaozhi.common.constant;

public interface Constant {

    /** 超管标识 */
    int SUPER_ADMIN = 1;

    /** 绑定码长度 */
    int BIND_CODE_LENGTH = 6;

    /** Token 有效期（秒），7天 */
    long TOKEN_EXPIRE_SECONDS = 7 * 24 * 60 * 60;
}
