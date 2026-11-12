package com.yourpitbox.app;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/** PIN verifiers remain inside the Android Keystore-encrypted profile index. */
public final class PinCredential {
    private PinCredential() {}
    public static String algorithm() {
        try { SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256"); return "PBKDF2WithHmacSHA256"; }
        catch (java.security.NoSuchAlgorithmException legacyAndroid) { return "PBKDF2WithHmacSHA1"; }
    }
    public static String derive(String pin, String salt) throws Exception { return derive(pin, salt, algorithm()); }
    public static String derive(String pin, String salt, String algorithm) throws Exception {
        if (!algorithm.equals("PBKDF2WithHmacSHA256") && !algorithm.equals("PBKDF2WithHmacSHA1")) throw new IllegalArgumentException("Invalid PIN verifier");
        if (pin == null || !pin.matches("[0-9]{4}")) throw new IllegalArgumentException("Enter exactly four digits.");
        PBEKeySpec spec = new PBEKeySpec(pin.toCharArray(), salt.getBytes(StandardCharsets.UTF_8), algorithm.equals("PBKDF2WithHmacSHA1") ? 600000 : 120000, 256);
        try {
            byte[] bytes = SecretKeyFactory.getInstance(algorithm).generateSecret(spec).getEncoded();
            StringBuilder hex = new StringBuilder();
            for (byte b : bytes) hex.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
            return hex.toString();
        } finally { spec.clearPassword(); }
    }
    public static boolean matches(String pin, String salt, String algorithm, String expected) throws Exception {
        return MessageDigest.isEqual(derive(pin, salt, algorithm).getBytes(StandardCharsets.UTF_8), expected.getBytes(StandardCharsets.UTF_8));
    }
}
