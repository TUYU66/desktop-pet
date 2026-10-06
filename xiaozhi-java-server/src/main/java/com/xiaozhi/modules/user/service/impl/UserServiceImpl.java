package com.xiaozhi.modules.user.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.xiaozhi.common.constant.Constant;
import com.xiaozhi.common.exception.BusinessException;
import com.xiaozhi.common.exception.ErrorCode;
import com.xiaozhi.modules.user.dao.UserDao;
import com.xiaozhi.modules.user.entity.UserEntity;
import com.xiaozhi.modules.user.service.UserService;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

@Service
public class UserServiceImpl extends ServiceImpl<UserDao, UserEntity> implements UserService {

    private static final BCryptPasswordEncoder ENCODER = new BCryptPasswordEncoder();

    @Override
    public UserEntity findByUsername(String username) {
        return this.getOne(new LambdaQueryWrapper<UserEntity>()
                .eq(UserEntity::getUsername, username));
    }

    @Override
    public UserEntity findById(Long id) {
        return this.getById(id);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean register(String username, String password) {
        // 检查用户名是否已存在
        UserEntity existing = findByUsername(username);
        if (existing != null) {
            throw new BusinessException(ErrorCode.USERNAME_EXISTS);
        }

        // SM2 解密密码（前端加密传输）
        String plainPassword = password;
        try {
            // 如果密码以 SM2 密文格式开头，尝试解密
            if (password.length() > 64) {
                // 使用 hutool SM2 解密（实际部署需要配置公私钥对）
                // plainPassword = sm2.decryptStr(password, KeyType.PrivateKey);
            }
        } catch (Exception ignored) {
            // 解密失败则使用原始密码
        }

        UserEntity user = new UserEntity();
        user.setUsername(username);
        user.setPassword(ENCODER.encode(plainPassword));
        user.setStatus(1);
        user.setCreateDate(LocalDateTime.now());
        user.setUpdateDate(LocalDateTime.now());

        // 第一个注册的用户自动成为超级管理员
        long count = this.count();
        if (count == 0) {
            user.setSuperAdmin(Constant.SUPER_ADMIN);
        } else {
            user.setSuperAdmin(0);
        }

        return this.save(user);
    }

    @Override
    public void updatePassword(Long userId, String currentPassword, String newPassword) {
        UserEntity user = this.getById(userId);
        if (user == null) {
            throw new BusinessException(ErrorCode.USER_NOT_FOUND);
        }
        if (!ENCODER.matches(currentPassword, user.getPassword())) {
            throw new BusinessException(ErrorCode.PASSWORD_ERROR);
        }
        user.setPassword(ENCODER.encode(newPassword));
        user.setUpdateDate(LocalDateTime.now());
        this.updateById(user);
    }

    @Override
    public void updateUser(UserEntity user) {
        user.setUpdateDate(LocalDateTime.now());
        this.updateById(user);
    }
}
