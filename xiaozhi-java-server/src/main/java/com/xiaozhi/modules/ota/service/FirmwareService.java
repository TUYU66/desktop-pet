package com.xiaozhi.modules.ota.service;

import cn.hutool.core.lang.UUID;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.xiaozhi.modules.ota.dao.FirmwareDao;
import com.xiaozhi.modules.ota.entity.FirmwareEntity;
import org.springframework.stereotype.Service;

import java.io.File;
import java.time.LocalDateTime;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class FirmwareService extends ServiceImpl<FirmwareDao, FirmwareEntity> {

    /** 下载 UUID → 固件 ID 映射 */
    private final Map<String, Long> downloadTokens = new ConcurrentHashMap<>();

    public Page<FirmwareEntity> page(int page, int size) {
        return this.page(new Page<>(page, size),
                new LambdaQueryWrapper<FirmwareEntity>()
                        .orderByDesc(FirmwareEntity::getCreateDate));
    }

    public FirmwareEntity getLatestVersion(String boardType) {
        return this.getOne(new LambdaQueryWrapper<FirmwareEntity>()
                .eq(FirmwareEntity::getBoardType, boardType)
                .eq(FirmwareEntity::getStatus, 1)
                .orderByDesc(FirmwareEntity::getCreateDate)
                .last("LIMIT 1"));
    }

    public String generateDownloadToken(Long firmwareId) {
        String uuid = UUID.fastUUID().toString(true);
        downloadTokens.put(uuid, firmwareId);
        return uuid;
    }

    public Long getFirmwareIdByToken(String uuid) {
        Long id = downloadTokens.get(uuid);
        if (id != null) {
            downloadTokens.remove(uuid);
        }
        return id;
    }

    public void incrementDownloadCount(Long firmwareId) {
        FirmwareEntity entity = this.getById(firmwareId);
        if (entity != null) {
            entity.setDownloadCount(entity.getDownloadCount() != null ? entity.getDownloadCount() + 1 : 1);
            this.updateById(entity);
        }
    }

    public void deleteFirmware(Long id) {
        FirmwareEntity entity = this.getById(id);
        if (entity == null) return;
        // 删除磁盘文件
        if (entity.getFilePath() != null) {
            File file = new File(entity.getFilePath());
            if (file.exists()) file.delete();
        }
        this.removeById(id);
    }
}
