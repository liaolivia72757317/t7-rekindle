package org.t7rekindle.server.config;

import java.time.Clock;
import org.springframework.boot.ApplicationRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.argon2.Argon2PasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.t7rekindle.server.service.AdminService;

@Configuration
public class AppConfig {
    @Bean PasswordEncoder passwords() { return new Argon2PasswordEncoder(16, 32, 1, 19456, 2); }
    @Bean Clock clock() { return Clock.systemUTC(); }
    @Bean TransactionTemplate transactions(PlatformTransactionManager manager) { return new TransactionTemplate(manager); }
    @Bean ApplicationRunner initializeAdmin(AdminService admins) {
        return args -> {
            if (args.containsOption("reset-admin") && !args.containsOption("confirm-reset-admin")) {
                throw new IllegalArgumentException("Reset requires --confirm-reset-admin");
            }
            String password = args.containsOption("reset-admin") ? admins.reset() : admins.initialize();
            if (password != null) {
                System.out.println("Initial administrator: admin\nOne-time password: " + password);
            }
        };
    }
}
