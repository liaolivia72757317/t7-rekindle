package org.t7rekindle.server;

import org.flywaydb.core.Flyway;
import org.springframework.boot.DefaultApplicationArguments;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.security.autoconfigure.UserDetailsServiceAutoConfiguration;

@SpringBootApplication(exclude = UserDetailsServiceAutoConfiguration.class)
public class ServerApplication {
    public static void main(String[] args) {
        var options = new DefaultApplicationArguments(args);
        if (options.containsOption("migrate")) {
            Flyway.configure().dataSource(requiredEnv("DB_URL"), requiredEnv("MIGRATION_USER"),
                    requiredEnv("MIGRATION_PASSWORD")).load().migrate();
            return;
        }
        var app = new SpringApplication(ServerApplication.class);
        if (options.containsOption("reset-admin")) {
            if (!options.containsOption("confirm-reset-admin")) {
                throw new IllegalArgumentException("Reset requires --confirm-reset-admin");
            }
            app.setWebApplicationType(WebApplicationType.NONE);
            try (var context = app.run(args)) {
                context.getBeanFactory();
            }
        } else {
            app.run(args);
        }
    }

    private static String requiredEnv(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("Missing environment variable: " + name);
        }
        return value;
    }
}
