package com.xiaozhi.common.base;

import com.baomidou.mybatisplus.core.handlers.MetaObjectHandler;
import org.apache.ibatis.reflection.MetaObject;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

@Component
public class MyMetaObjectHandler implements MetaObjectHandler {

    @Override
    public void insertFill(MetaObject metaObject) {
        if (getFieldValByName("createDate", metaObject) == null) {
            this.strictInsertFill(metaObject, "createDate", LocalDateTime.class, LocalDateTime.now());
        }
        if (getFieldValByName("updateDate", metaObject) == null) {
            this.strictInsertFill(metaObject, "updateDate", LocalDateTime.class, LocalDateTime.now());
        }
    }

    @Override
    public void updateFill(MetaObject metaObject) {
        this.strictUpdateFill(metaObject, "updateDate", LocalDateTime.class, LocalDateTime.now());
    }
}
