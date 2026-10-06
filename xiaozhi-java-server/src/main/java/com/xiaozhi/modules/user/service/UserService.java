package com.xiaozhi.modules.user.service;

import com.xiaozhi.modules.user.entity.UserEntity;

public interface UserService {

    UserEntity findByUsername(String username);

    UserEntity findById(Long id);

    boolean register(String username, String password);

    void updatePassword(Long userId, String currentPassword, String newPassword);

    void updateUser(UserEntity user);
}
