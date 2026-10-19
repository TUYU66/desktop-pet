package com.xiaozhi.common.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.config.CorsRegistry;
import org.springframework.web.reactive.config.WebFluxConfigurer;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.web.reactive.config.BlockingExecutionConfigurer;

@Configuration
public class WebFluxConfig implements WebFluxConfigurer {
    @Value("${XIAOZHI_CORS_ORIGINS:http://localhost:5173,http://127.0.0.1:5173}")
    private String origins;

    @Bean
    public ThreadPoolTaskExecutor blockingControllers() {
        var executor=new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(8);
        executor.setMaxPoolSize(16);
        executor.setQueueCapacity(128);
        executor.setThreadNamePrefix("controller-jdbc-");
        return executor;
    }

    @Override
    public void configureBlockingExecution(BlockingExecutionConfigurer configurer) {
        // WebFlux 6.1 routes synchronous controllers to this bounded executor.
        configurer.setExecutor(blockingControllers());
    }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOrigins(origins.split(","))
                .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS")
                .allowedHeaders("*")
                .allowCredentials(false)
                .maxAge(3600);
    }
}
