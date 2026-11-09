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
import org.springframework.jdbc.core.JdbcTemplate;
import jakarta.annotation.Resource;
import java.nio.charset.StandardCharsets;

import java.time.LocalDateTime;

@Service
public class UserServiceImpl extends ServiceImpl<UserDao, UserEntity> implements UserService {

    private static final BCryptPasswordEncoder ENCODER = new BCryptPasswordEncoder();
    @Resource private JdbcTemplate jdbc;

    @Override
    public RegistrationState registrationState() {
        long accounts = this.count(); // Disabled accounts also count as initialized.
        return new RegistrationState(accounts > 0, accounts == 0, accounts > 1);
    }

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
        if (username == null || username.isBlank() || username.length() > 50 ||
            password == null || password.isBlank() || password.length() < 6 ||
            password.getBytes(StandardCharsets.UTF_8).length > 72) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "用户名不超过50字符，密码至少6字符且不超过72字节");
        }
        // A permanent row exists even before the first account. Its lock lasts
        // until this transaction commits, including across server instances.
        jdbc.queryForObject("SELECT id FROM account_initialization_guard WHERE id=1 FOR UPDATE", Integer.class);
        long accounts = this.count();
        if (accounts > 1) throw new BusinessException(ErrorCode.ACCOUNT_CONFLICT);
        if (accounts > 0) throw new BusinessException(ErrorCode.ACCOUNT_INITIALIZED);

        UserEntity user = new UserEntity();
        user.setUsername(username);
        user.setPassword(ENCODER.encode(password));
        user.setStatus(1);
        user.setCreateDate(LocalDateTime.now());
        user.setUpdateDate(LocalDateTime.now());

        user.setSuperAdmin(Constant.SUPER_ADMIN);
        if (!this.save(user)) throw new BusinessException(ErrorCode.INTERNAL_ERROR);
        return true;
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
