package org.t7rekindle.server.web;

import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.transaction.CannotCreateTransactionException;
import org.t7rekindle.server.domain.Problem;
import org.t7rekindle.server.service.AccountService;
import org.t7rekindle.server.service.AdminService;
import org.t7rekindle.server.service.AuthService;
import org.t7rekindle.server.service.PresenceService;
import tools.jackson.databind.json.JsonMapper;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class WebErrorsTest {
    private final AuthService auth = mock(AuthService.class);
    private final AdminService admins = mock(AdminService.class);
    private final MockMvc mvc = MockMvcBuilders.standaloneSetup(
            new ApiController(auth, mock(PresenceService.class)),
            new AdminController(admins, mock(AccountService.class), mock(SecretResults.class)))
            .setControllerAdvice(new WebErrors(JsonMapper.builder().build())).build();

    @Test void transactionAndDataAccessFailuresReturnUnavailableBody() throws Exception {
        for (RuntimeException failure : new RuntimeException[] {
                new CannotCreateTransactionException("database unavailable"),
                new DataAccessResourceFailureException("database unavailable")}) {
            when(auth.login(anyString(), anyString(), anyString())).thenThrow(failure);
            mvc.perform(post("/api/v1/auth/sessions").contentType("application/json")
                    .content("{\"loginName\":\"sample\",\"password\":\"TEST_PASSWORD\"}"))
                    .andExpect(status().isServiceUnavailable())
                    .andExpect(header().string("Cache-Control", "no-store"))
                    .andExpect(jsonPath("$.code").value("SERVICE_UNAVAILABLE"))
                    .andExpect(jsonPath("$.message").value(Problem.unavailable().getMessage()))
                    .andExpect(jsonPath("$.requestId").isNotEmpty())
                    .andExpect(jsonPath("$.retryable").value(true));
            reset(auth);
        }
    }

    @Test void bothLoginEndpointsReturnRetryAfterWhenLimited() throws Exception {
        var limited = new Problem(429, "RATE_LIMITED", "请稍后重试");
        when(auth.login(anyString(), anyString(), anyString())).thenThrow(limited);
        when(admins.login(anyString(), anyString(), anyString())).thenThrow(limited);
        mvc.perform(post("/api/v1/auth/sessions").contentType("application/json")
                .content("{\"loginName\":\"sample\",\"password\":\"TEST_PASSWORD\"}"))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().string("Retry-After", "300"))
                .andExpect(jsonPath("$.retryable").value(true));
        mvc.perform(post("/admin/login").param("loginName", "admin").param("password", "TEST_PASSWORD"))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().string("Retry-After", "300"))
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(view().name("error"))
                .andExpect(model().attribute("message", limited.getMessage()));
    }
}
