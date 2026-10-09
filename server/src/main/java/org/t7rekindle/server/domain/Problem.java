package org.t7rekindle.server.domain;

public final class Problem extends RuntimeException {
    private static final long serialVersionUID = 1L;
    private final int status;
    private final String code;

    public Problem(int status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }
    public int status() { return status; }
    public String code() { return code; }
    public static Problem unauthorized() { return new Problem(401, "INVALID_CREDENTIALS", "凭证无效或已过期"); }
    public static Problem invalid() { return new Problem(400, "INVALID_INPUT", "请检查输入格式"); }
    public static Problem unavailable() { return new Problem(503, "SERVICE_UNAVAILABLE", "服务暂不可用，请稍后重试"); }
    public static Problem missing() { return new Problem(404, "NOT_FOUND", "记录不存在"); }
}
