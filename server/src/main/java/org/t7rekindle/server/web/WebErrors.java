package org.t7rekindle.server.web;

import java.io.IOException;
import java.util.UUID;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.dao.DataAccessException;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.stereotype.Component;
import org.springframework.transaction.TransactionException;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.ModelAndView;
import org.t7rekindle.server.domain.Problem;
import tools.jackson.databind.json.JsonMapper;

@Component
@ControllerAdvice
public class WebErrors {
    public record ErrorBody(String code, String message, String requestId, boolean retryable) { }
    private final JsonMapper json;

    public WebErrors(JsonMapper json) { this.json = json; }
    public void write(HttpServletResponse response, Problem error) throws IOException {
        response.setStatus(error.status());
        response.setContentType("application/json;charset=UTF-8");
        response.setHeader("Cache-Control", "no-store");
        if (error.status() == 429) response.setHeader("Retry-After", "300");
        if (error.status() == 401) response.setHeader("WWW-Authenticate", "Bearer");
        json.writeValue(response.getWriter(), body(error));
    }
    @ExceptionHandler({Problem.class, DataAccessException.class, TransactionException.class, HttpMessageNotReadableException.class,
            MethodArgumentTypeMismatchException.class})
    public ModelAndView handle(Exception exception, HttpServletRequest request, HttpServletResponse response) throws IOException {
        Problem problem = switch (exception) {
            case Problem value -> value;
            case DataAccessException ignored -> Problem.unavailable();
            case TransactionException ignored -> Problem.unavailable();
            default -> Problem.invalid();
        };
        if (request.getRequestURI().startsWith("/api/")) {
            write(response, problem);
            return null;
        }
        response.setStatus(problem.status());
        response.setHeader("Cache-Control", "no-store");
        if (problem.status() == 429) response.setHeader("Retry-After", "300");
        return new ModelAndView("error", "message", problem.getMessage());
    }
    private ErrorBody body(Problem error) {
        return new ErrorBody(error.code(), error.getMessage(), UUID.randomUUID().toString(),
                error.status() == 503 || error.status() == 429);
    }
}
