package org.interestslab.t7rekindle.server.service;

import java.nio.charset.Charset;
import org.interestslab.t7rekindle.server.domain.Problem;

public final class GameNameRules {
    private static final Charset GBK = Charset.forName("GBK");
    private GameNameRules() { }

    public static String normalize(String value) {
        if (value == null) throw invalid();
        int start = 0, end = value.length();
        while (start < end && whitespace(value.charAt(start))) start++;
        while (end > start && whitespace(value.charAt(end - 1))) end--;
        String name = value.substring(start, end);
        if (name.isEmpty() || name.length() > 31 || name.chars().anyMatch(c -> Character.isISOControl(c)
                || c == 0x20ac || c >= 0xe000 && c <= 0xf8ff)
                || !GBK.newEncoder().canEncode(name)) throw invalid();
        if (name.getBytes(GBK).length > 31) throw invalid();
        return name;
    }
    // Match .NET String.Trim, including NEL and Unicode space separators.
    private static boolean whitespace(char c) {
        return c >= 9 && c <= 13 || Character.isSpaceChar(c) || c == 0x85;
    }
    private static Problem invalid() {
        return new Problem(400, "INVALID_GAME_NAME", "请输入游戏支持的名称，最多 31 个 GBK 字节，不含控制字符、emoji 或私用字符");
    }
    public static Problem exists() { return new Problem(409, "GAME_NAME_EXISTS", "游戏名称已被使用，请换一个名称"); }
    public static void requireRegistered(String name) {
        if (name == null || name.isEmpty()) throw new Problem(409, "REGISTRATION_REQUIRED", "请先设置游戏名称，完成注册");
    }
}
