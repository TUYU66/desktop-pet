package com.xiaozhi.modules.user;

import com.xiaozhi.common.exception.BusinessException;
import com.xiaozhi.common.exception.ErrorCode;
import com.xiaozhi.modules.user.entity.UserEntity;
import com.xiaozhi.modules.user.service.UserService;
import com.xiaozhi.modules.user.service.impl.UserServiceImpl;
import liquibase.integration.spring.SpringLiquibase;
import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.aop.framework.ProxyFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.annotation.AnnotationTransactionAttributeSource;
import org.springframework.transaction.interceptor.TransactionInterceptor;
import java.util.ArrayList;
import java.util.UUID;
import java.util.concurrent.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.any;

class SingleAccountRegistrationTest {
    JdbcTemplate jdbc;
    JdbcDataSource dataSource;

    @BeforeEach void database() throws Exception {
        dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:account_" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1;LOCK_TIMEOUT=10000");
        jdbc = new JdbcTemplate(dataSource);
        jdbc.execute("CREATE TABLE sys_user(id BIGINT AUTO_INCREMENT PRIMARY KEY,username VARCHAR(50) UNIQUE,password VARCHAR(200),status INT,super_admin INT)");
        var migration = new SpringLiquibase();
        migration.setDataSource(dataSource);
        migration.setChangeLog("classpath:db/changelog/v1.9.7_single_account.xml");
        migration.afterPropertiesSet();
    }

    UserService service(boolean failAfterInsert) {
        var implementation = spy(new UserServiceImpl());
        ReflectionTestUtils.setField(implementation, "jdbc", jdbc);
        doAnswer(call -> jdbc.queryForObject("SELECT COUNT(*) FROM sys_user", Long.class)).when(implementation).count();
        doAnswer(call -> {
            UserEntity user = call.getArgument(0);
            jdbc.update("INSERT INTO sys_user(username,password,status,super_admin) VALUES (?,?,?,?)",
                user.getUsername(), user.getPassword(), user.getStatus(), user.getSuperAdmin());
            if (failAfterInsert) throw new IllegalStateException("simulated write failure");
            return true;
        }).when(implementation).save(any(UserEntity.class));
        var proxy = new ProxyFactory(implementation);
        proxy.setProxyTargetClass(true);
        proxy.addAdvice(new TransactionInterceptor(new DataSourceTransactionManager(dataSource), new AnnotationTransactionAttributeSource()));
        return (UserService) proxy.getProxy();
    }

    @Test void initializesOneAdministratorAndClosesRegistration() {
        var service = service(false);
        assertTrue(service.registrationState().registrationOpen());
        assertTrue(service.register("owner", "secret123"));
        var state = service.registrationState();
        assertTrue(state.initialized());assertFalse(state.registrationOpen());assertFalse(state.accountConflict());
        assertEquals(1, jdbc.queryForObject("SELECT super_admin FROM sys_user", Integer.class));
        assertTrue(new BCryptPasswordEncoder().matches("secret123", jdbc.queryForObject("SELECT password FROM sys_user", String.class)));
        assertEquals(ErrorCode.ACCOUNT_INITIALIZED.getCode(), assertThrows(BusinessException.class,
            () -> service.register("another", "secret123")).getCode());
        assertEquals(1L, jdbc.queryForObject("SELECT COUNT(*) FROM sys_user", Long.class));
    }

    @Test void disabledAccountDoesNotReopenRegistrationAndMultipleAccountsAreReported() {
        var service = service(false);service.register("owner", "secret123");
        jdbc.update("UPDATE sys_user SET status=0");
        assertFalse(service.registrationState().registrationOpen());
        assertThrows(BusinessException.class, () -> service.register("second", "secret123"));
        jdbc.update("INSERT INTO sys_user(username,password,status,super_admin) VALUES ('legacy','unchanged',1,0)");
        assertTrue(service.registrationState().accountConflict());
        assertEquals(ErrorCode.ACCOUNT_CONFLICT.getCode(), assertThrows(BusinessException.class,
            () -> service.register("third", "secret123")).getCode());
        assertEquals(2L, jdbc.queryForObject("SELECT COUNT(*) FROM sys_user", Long.class));
        assertEquals("unchanged", jdbc.queryForObject("SELECT password FROM sys_user WHERE username='legacy'", String.class));
    }

    @Test void concurrentRequestsAcrossTwoServiceInstancesCreateExactlyOneAccount() throws Exception {
        var first = service(false);var second = service(false);
        var start = new CountDownLatch(1);
        var executor = Executors.newFixedThreadPool(8);
        var requests = new ArrayList<Future<Boolean>>();
        try {
            for (int i=0; i<8; i++) {
                final int index=i;
                requests.add(executor.submit(() -> {
                    start.await();
                    try {return (index%2==0?first:second).register("user"+index, "secret123");}
                    catch (BusinessException error) {assertEquals(ErrorCode.ACCOUNT_INITIALIZED.getCode(), error.getCode());return false;}
                }));
            }
            start.countDown();int successful=0;
            for (var request: requests) if(request.get(15,TimeUnit.SECONDS)) successful++;
            assertEquals(1,successful);
            assertEquals(1L,jdbc.queryForObject("SELECT COUNT(*) FROM sys_user",Long.class));
        } finally {executor.shutdownNow();}
    }

    @Test void failedInsertRollsBackAndAllowsRetryWithoutLeavingTheLockHeld() {
        assertThrows(IllegalStateException.class, () -> service(true).register("owner", "secret123"));
        assertEquals(0L,jdbc.queryForObject("SELECT COUNT(*) FROM sys_user",Long.class));
        assertTrue(service(false).register("owner", "secret123"));
    }

    @Test void rejectsBlankShortAndBcryptTruncatedPasswordsWithoutCreatingAnAccount() {
        var service=service(false);
        assertThrows(BusinessException.class, () -> service.register(" ", "secret123"));
        assertThrows(BusinessException.class, () -> service.register("owner", "short"));
        assertThrows(BusinessException.class, () -> service.register("owner", "密".repeat(25)));
        assertTrue(service.registrationState().registrationOpen());
    }
}
