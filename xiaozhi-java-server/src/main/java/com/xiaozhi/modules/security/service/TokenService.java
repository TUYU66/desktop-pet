package com.xiaozhi.modules.security.service;

import cn.hutool.core.util.IdUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.xiaozhi.common.exception.BusinessException;
import com.xiaozhi.common.exception.ErrorCode;
import com.xiaozhi.modules.security.dao.UserTokenDao;
import com.xiaozhi.modules.security.entity.UserTokenEntity;
import com.xiaozhi.modules.user.entity.UserEntity;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

import static com.xiaozhi.common.constant.Constant.TOKEN_EXPIRE_SECONDS;

@Service
public class TokenService {

    @Resource
    private UserTokenDao userTokenDao;

    /**
     * 生成 token
     */
    public String createToken(UserEntity user) {
        // 清理旧 token
        userTokenDao.delete(new LambdaQueryWrapper<UserTokenEntity>()
                .eq(UserTokenEntity::getUserId, user.getId()));

        String token = IdUtil.fastSimpleUUID();
        UserTokenEntity tokenEntity = new UserTokenEntity();
        tokenEntity.setUserId(user.getId());
        tokenEntity.setToken(token);
        tokenEntity.setExpireDate(LocalDateTime.now().plusSeconds(TOKEN_EXPIRE_SECONDS));
        tokenEntity.setCreateDate(LocalDateTime.now());
        tokenEntity.setUpdateDate(LocalDateTime.now());
        userTokenDao.insert(tokenEntity);

        return token;
    }

    /**
     * 根据 token 获取用户 ID
     */
    public Long getUserIdByToken(String token) {
        UserTokenEntity tokenEntity = userTokenDao.selectOne(
                new LambdaQueryWrapper<UserTokenEntity>()
                        .eq(UserTokenEntity::getToken, token)
                        .gt(UserTokenEntity::getExpireDate, LocalDateTime.now())
        );
        if (tokenEntity == null) {
            throw new BusinessException(ErrorCode.UNAUTHORIZED);
        }
        return tokenEntity.getUserId();
    }

    /**
     * 登出
     */
    public void logout(String token) {
        userTokenDao.delete(new LambdaQueryWrapper<UserTokenEntity>()
                .eq(UserTokenEntity::getToken, token));
    }
}
