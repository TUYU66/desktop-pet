package com.xiaozhi.modules.security.controller;

import com.xiaozhi.common.exception.BusinessException;
import com.xiaozhi.common.exception.ErrorCode;
import com.xiaozhi.common.exception.GlobalExceptionHandler;
import com.xiaozhi.modules.user.service.UserService;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import com.xiaozhi.modules.security.service.TokenService;
import org.springframework.test.web.reactive.server.WebTestClient;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.anyString;

class AccountInitializationApiTest {
    final UserService users=mock(UserService.class);
    final TokenService tokens=mock(TokenService.class);
    WebTestClient client() {
        return WebTestClient.bindToController(new AuthController(users,tokens))
            .controllerAdvice(new GlobalExceptionHandler()).build();
    }

    @Test void statusReportsInitializationWithoutExposingAccountDetails() {
        when(users.registrationState()).thenReturn(new UserService.RegistrationState(false,true,false));
        client().get().uri("/xiaozhi/api/auth/registration-status").exchange().expectStatus().isOk()
            .expectBody().jsonPath("$.data.registrationOpen").isEqualTo(true)
            .jsonPath("$.data.initialized").isEqualTo(false).jsonPath("$.data.username").doesNotExist();
        when(users.registrationState()).thenReturn(new UserService.RegistrationState(true,false,true));
        client().get().uri("/xiaozhi/api/auth/registration-status").exchange().expectStatus().isOk()
            .expectBody().jsonPath("$.data.registrationOpen").isEqualTo(false)
            .jsonPath("$.data.accountConflict").isEqualTo(true);
    }

    @Test void legacyAccountConflictBlocksLoginBeforeCheckingCredentials() {
        when(users.registrationState()).thenReturn(new UserService.RegistrationState(true,false,true));
        client().post().uri("/xiaozhi/api/auth/login").contentType(MediaType.APPLICATION_JSON)
            .bodyValue(java.util.Map.of("username","legacy","password","secret123"))
            .exchange().expectStatus().isOk().expectBody().jsonPath("$.code").isEqualTo(1005);
        verify(users,never()).findByUsername(anyString());
        verifyNoInteractions(tokens);
    }

    @Test void directRegistrationRequestIsRejectedAfterInitialization() {
        when(users.register(anyString(),anyString())).thenThrow(new BusinessException(ErrorCode.ACCOUNT_INITIALIZED));
        client().post().uri("/xiaozhi/api/auth/register").contentType(MediaType.APPLICATION_JSON)
            .bodyValue("{\"username\":\"second\",\"password\":\"secret123\"}")
            .exchange().expectStatus().isOk().expectBody().jsonPath("$.code").isEqualTo(1004);
    }

    @Test void invalidRequestIsRejectedBeforeRegistration() {
        client().post().uri("/xiaozhi/api/auth/register").contentType(MediaType.APPLICATION_JSON)
            .bodyValue("{\"username\":\"\",\"password\":\"short\"}")
            .exchange().expectStatus().isOk().expectBody().jsonPath("$.code").isEqualTo(400);
        verifyNoInteractions(users);
    }


}
