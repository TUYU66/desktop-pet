package com.xiaozhi.modules.security.controller;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.common.exception.BusinessException;
import com.xiaozhi.common.exception.ErrorCode;
import com.xiaozhi.modules.security.service.TokenService;
import com.xiaozhi.modules.user.entity.UserEntity;
import com.xiaozhi.modules.user.service.UserService;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Size;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;
import lombok.Data;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/xiaozhi/api/auth")
public class AuthController {

    private final UserService userService;
    private final TokenService tokenService;

    public AuthController(UserService userService, TokenService tokenService) {
        this.userService = userService;
        this.tokenService = tokenService;
    }

    @PostMapping("/login")
    public Result<Map<String, Object>> login(@RequestBody LoginRequest req) {
        if (userService.registrationState().accountConflict()) throw new BusinessException(ErrorCode.ACCOUNT_CONFLICT);
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

    @GetMapping("/registration-status")
    public Mono<Result<UserService.RegistrationState>> registrationStatus() {
        return Mono.fromCallable(() -> Result.ok(userService.registrationState()))
            .subscribeOn(Schedulers.boundedElastic());
    }

    @PostMapping("/register")
    public Mono<Result<Void>> register(@Valid @RequestBody RegisterRequest req) {
        return Mono.fromCallable(() -> {
            userService.register(req.getUsername(), req.getPassword());
            return Result.<Void>ok();
        }).subscribeOn(Schedulers.boundedElastic());
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
    @Size(max = 50, message = "用户名不超过50字符")
    private String username;
    @NotBlank(message = "密码不能为空")
    @Size(min = 6, max = 72, message = "密码至少6字符且不超过72字符")
    private String password;
    private String email;
    private String mobile;
}
