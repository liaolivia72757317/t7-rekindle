package org.t7rekindle.server.service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

@Component
public class Secrets {
    private final SecureRandom random = new SecureRandom();
    private final PasswordEncoder passwords;
    private final String dummyHash;

    public Secrets(PasswordEncoder passwords) {
        this.passwords = passwords;
        dummyHash = passwords.encode(randomToken());
    }
    public String randomToken() {
        byte[] bytes = new byte[32];
        random.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
    public String encode(String password) { return passwords.encode(password); }
    public boolean matches(String password, String hash) {
        return passwords.matches(password, hash == null ? dummyHash : hash);
    }
    public static String hash(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is required", error);
        }
    }
}
