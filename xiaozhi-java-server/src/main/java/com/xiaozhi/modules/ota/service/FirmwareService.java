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
    private java.time.Clock clock=java.time.Clock.systemUTC();

    /** 下载 UUID → 固件 ID 映射 */
    private record DownloadGrant(Long firmwareId,long expiresAt) {}
    private final Map<String, DownloadGrant> downloadTokens = new ConcurrentHashMap<>();

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
        long now=clock.millis();
        downloadTokens.entrySet().removeIf(entry->entry.getValue().expiresAt()<=now);
        if(downloadTokens.size()>=4096) throw new IllegalStateException("下载授权繁忙，请稍后重试");
        String uuid = UUID.fastUUID().toString(true);
        downloadTokens.put(uuid, new DownloadGrant(firmwareId,now+300_000));
        return uuid;
    }

    public Long getFirmwareIdByToken(String uuid) {
        DownloadGrant grant = downloadTokens.remove(uuid);
        return grant!=null&&grant.expiresAt()>clock.millis()?grant.firmwareId():null;
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
