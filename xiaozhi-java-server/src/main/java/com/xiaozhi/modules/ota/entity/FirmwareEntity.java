package com.xiaozhi.modules.ota.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.xiaozhi.common.base.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
@TableName("ota_firmware")
public class FirmwareEntity extends BaseEntity {

    private String version;
    private String description;
    private String filePath;
    private Long fileSize;
    private String md5;
    private String boardType;
    private Integer forceUpdate;
    private Integer status;
    private Integer downloadCount;
}
