package org.t7rekindle.server.config;

import java.io.IOException;
import java.util.List;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpMethod;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.AnonymousAuthenticationFilter;
import org.springframework.web.filter.OncePerRequestFilter;
import org.t7rekindle.server.domain.Models.AdminIdentity;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.service.AdminService;
import org.t7rekindle.server.service.AuthService;
import org.t7rekindle.server.web.WebErrors;

@Configuration
@ConditionalOnWebApplication(type = ConditionalOnWebApplication.Type.SERVLET)
public class SecurityConfig {
    public static final String ADMIN_IDENTITY = "adminIdentity";

    @Bean @Order(1)
    SecurityFilterChain api(HttpSecurity http, AuthService auth, WebErrors errors) throws Exception {
        http.securityMatcher("/api/**")
                .csrf(csrf -> csrf.disable())
                .requestCache(cache -> cache.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(rules -> rules.requestMatchers(HttpMethod.POST, "/api/v1/auth/sessions").permitAll()
                        .anyRequest().authenticated())
                .exceptionHandling(handling -> handling
                        .authenticationEntryPoint((request, response, error) -> errors.write(response, Problem.unauthorized()))
                        .accessDeniedHandler((request, response, error) -> errors.write(response,
                                new Problem(403, "FORBIDDEN", "此身份没有访问权限"))))
                .addFilterBefore(new ApiFilter(auth, errors), AnonymousAuthenticationFilter.class);
        return http.build();
    }

    @Bean @Order(2)
    SecurityFilterChain admin(HttpSecurity http, AdminService admins) throws Exception {
        http.securityMatcher("/admin/**")
                .requestCache(cache -> cache.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED))
                .authorizeHttpRequests(rules -> rules.requestMatchers("/admin/login", "/admin/assets/**").permitAll()
                        .anyRequest().hasRole("ADMIN"))
                .exceptionHandling(handling -> handling
                        .authenticationEntryPoint((request, response, error) -> response.sendRedirect("/admin/login")))
                .headers(headers -> headers.contentSecurityPolicy(policy -> policy.policyDirectives(
                        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; "
                                + "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")))
                .addFilterBefore(new AdminFilter(admins), AnonymousAuthenticationFilter.class);
        return http.build();
    }

    @Bean @Order(3)
    SecurityFilterChain other(HttpSecurity http) throws Exception {
        http.sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(rules -> rules.requestMatchers("/actuator/health", "/error").permitAll()
                        .anyRequest().denyAll());
        return http.build();
    }

    private static void authenticate(Object principal, String role) {
        var context = SecurityContextHolder.createEmptyContext();
        context.setAuthentication(UsernamePasswordAuthenticationToken.authenticated(principal, null,
                List.of(new SimpleGrantedAuthority(role))));
        SecurityContextHolder.setContext(context);
    }

    static final class ApiFilter extends OncePerRequestFilter {
        private final AuthService auth;
        private final WebErrors errors;
        ApiFilter(AuthService auth, WebErrors errors) { this.auth = auth; this.errors = errors; }

        @Override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                FilterChain chain) throws ServletException, IOException {
            response.setHeader("Cache-Control", "no-store");
            if (!(request.getMethod().equals("POST") && request.getRequestURI().equals("/api/v1/auth/sessions"))) {
                try {
                    String authorization = request.getHeader("Authorization");
                    if (authorization == null || !authorization.startsWith("Bearer ")) throw Problem.unauthorized();
                    authenticate(auth.authenticate(authorization.substring(7)), "ROLE_ACCOUNT");
                } catch (Problem error) {
                    errors.write(response, error);
                    return;
                } catch (DataAccessException error) {
                    errors.write(response, Problem.unavailable());
                    return;
                }
            }
            chain.doFilter(request, response);
        }
    }

    static final class AdminFilter extends OncePerRequestFilter {
        private final AdminService admins;
        AdminFilter(AdminService admins) { this.admins = admins; }

        @Override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                FilterChain chain) throws ServletException, IOException {
            response.setHeader("Cache-Control", "no-store");
            var session = request.getSession(false);
            if (session != null && session.getAttribute(ADMIN_IDENTITY) instanceof AdminIdentity identity) {
                try {
                    AdminIdentity current = admins.current(identity);
                    authenticate(current, "ROLE_ADMIN");
                    String path = request.getRequestURI();
                    if (current.mustChangePassword() && !path.equals("/admin/password")
                            && !path.equals("/admin/logout") && !path.startsWith("/admin/assets/")) {
                        response.sendRedirect("/admin/password");
                        return;
                    }
                } catch (Problem error) {
                    session.invalidate();
                    response.sendRedirect("/admin/login");
                    return;
                } catch (DataAccessException error) {
                    response.sendError(503);
                    return;
                }
            }
            chain.doFilter(request, response);
        }
    }
}
