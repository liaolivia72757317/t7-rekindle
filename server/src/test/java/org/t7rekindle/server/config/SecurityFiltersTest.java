package org.t7rekindle.server.config;

import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.core.context.SecurityContextHolder;
import org.t7rekindle.server.domain.Models.AdminIdentity;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.service.AdminService;
import org.t7rekindle.server.service.AuthService;
import org.t7rekindle.server.web.WebErrors;
import tools.jackson.databind.json.JsonMapper;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class SecurityFiltersTest {
    @AfterEach void clear() { SecurityContextHolder.clearContext(); }

    @Test void apiDatabaseFailureDoesNotAuthenticateOrRunHandler() throws Exception {
        var auth = mock(AuthService.class);
        var chain = mock(FilterChain.class);
        var filter = new SecurityConfig.ApiFilter(auth, new WebErrors(JsonMapper.builder().build()));
        var request = new MockHttpServletRequest("GET", "/api/v1/auth/session");
        request.addHeader("Authorization", "Bearer " + "A".repeat(43));
        when(auth.authenticate(any())).thenThrow(new DataAccessResourceFailureException("database offline"));
        var response = new MockHttpServletResponse();
        filter.doFilter(request, response, chain);
        assertThat(response.getStatus()).isEqualTo(503);
        assertThat(response.getContentAsString()).contains("SERVICE_UNAVAILABLE", "\"retryable\":true").doesNotContain("database offline");
        verifyNoInteractions(chain);
        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
    }

    @Test void malformedAuthorizationAndRevokedCredentialsGetUniform401() throws Exception {
        var auth = mock(AuthService.class);
        var filter = new SecurityConfig.ApiFilter(auth, new WebErrors(JsonMapper.builder().build()));
        var request = new MockHttpServletRequest("GET", "/api/v1/auth/session");
        request.addHeader("Authorization", "Basic TOKEN");
        var response = new MockHttpServletResponse();
        filter.doFilter(request, response, mock(FilterChain.class));
        assertThat(response.getStatus()).isEqualTo(401);
        request.removeHeader("Authorization");
        request.addHeader("Authorization", "Bearer TOKEN");
        when(auth.authenticate(any())).thenThrow(Problem.unauthorized());
        response = new MockHttpServletResponse();
        filter.doFilter(request, response, mock(FilterChain.class));
        assertThat(response.getHeader("WWW-Authenticate")).isEqualTo("Bearer");
    }

    @Test void adminRevocationInvalidatesSessionAndDatabaseFailureDeniesAccess() throws Exception {
        var admins = mock(AdminService.class);
        var filter = new SecurityConfig.AdminFilter(admins);
        var request = new MockHttpServletRequest("GET", "/admin/accounts");
        var session = new MockHttpSession();
        session.setAttribute(SecurityConfig.ADMIN_IDENTITY, new AdminIdentity(0, false));
        request.setSession(session);
        when(admins.current(any())).thenThrow(new DataAccessResourceFailureException("offline"));
        var response = new MockHttpServletResponse();
        var chain = mock(FilterChain.class);
        filter.doFilter(request, response, chain);
        assertThat(response.getStatus()).isEqualTo(503);
        verifyNoInteractions(chain);
        doThrow(Problem.unauthorized()).when(admins).current(any());
        response = new MockHttpServletResponse();
        filter.doFilter(request, response, chain);
        assertThat(session.isInvalid()).isTrue();
        assertThat(response.getRedirectedUrl()).isEqualTo("/admin/login");
    }
}
