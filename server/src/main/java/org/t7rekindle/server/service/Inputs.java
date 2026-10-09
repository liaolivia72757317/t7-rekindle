package org.t7rekindle.server.service;

import org.t7rekindle.server.domain.Problem;

public final class Inputs {
    private Inputs() { }
    public static void loginName(String value) {
        if (value == null || !value.matches("[a-z0-9_.-]{3,64}")) throw Problem.invalid();
    }
    public static void password(String value) {
        if (value == null || value.length() < 8 || value.length() > 128) throw Problem.invalid();
    }
    public static int offset(int page) {
        if (page < 0 || page > 100_000) throw Problem.invalid();
        return page * 50;
    }
    public static String prefix(String query) {
        if (query == null || !query.matches("[a-z0-9_.-]{0,64}")) throw Problem.invalid();
        return query.replace("_", "\\_");
    }
}
