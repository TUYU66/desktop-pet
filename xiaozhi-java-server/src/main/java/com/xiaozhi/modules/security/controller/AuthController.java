package com.xiaozhi.modules.security.controller;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.common.exception.BusinessException;
import com.xiaozhi.common.exception.ErrorCode;
import com.xiaozhi.modules.security.service.TokenService;
import com.xiaozhi.modules.user.entity.UserEntity;
import com.xiaozhi.modules.user.service.UserService;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/xiaozhi/api/auth")
public class AuthController {

    @Resource
    private UserService userService;

    @Resource
    private TokenService tokenService;

    @PostMapping("/login")
    public Result<Map<String, Object>> login(@RequestBody LoginRequest req) {
        UserEntity user = userService.findByUsername(req.getUsername());
        if (user == null) {
            throw new BusinessException(ErrorCode.USER_NOT_FOUND);
        }
        if (user.getStatus() == 0) {
            throw new BusinessException(ErrorCode.FORBIDDEN);
        }

        // BCrypt 密码验证
        BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();
        if (!encoder.matches(req.getPassword(), user.getPassword())) {
            throw new BusinessException(ErrorCode.PASSWORD_ERROR);
        }
        String token = tokenService.createToken(user);

        Map<String, Object> data = new HashMap<>();
        data.put("token", token);
        data.put("userId", user.getId());
        data.put("username", user.getUsername());
        data.put("superAdmin", user.getSuperAdmin());
        return Result.ok(data);
    }

    @PostMapping("/register")
    public Result<?> register(@RequestBody RegisterRequest req) {
        userService.register(req.getUsername(), req.getPassword());
        return Result.ok();
    }

    @PostMapping("/logout")
    public Result<?> logout(@RequestHeader("Authorization") String auth) {
        if (auth != null && auth.startsWith("Bearer ")) {
            tokenService.logout(auth.substring(7));
        }
        return Result.ok();
    }
}

@Data
class LoginRequest {
    @NotBlank(message = "用户名不能为空")
    private String username;
    @NotBlank(message = "密码不能为空")
    private String password;
    private String captchaId;
    private String captcha;
}

@Data
class RegisterRequest {
    @NotBlank(message = "用户名不能为空")
    private String username;
    @NotBlank(message = "密码不能为空")
    private String password;
    private String email;
    private String mobile;
}
