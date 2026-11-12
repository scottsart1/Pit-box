package com.yourpitbox.app;

import java.security.SecureRandom;

/** Process-local authorization, never accepted from Intent extras or WebView JS. */
public final class AccountSession {
    private static volatile boolean unlocked;
    private static String profileId;
    private static boolean legacyProfile;
    private static final String LOCAL_TOKEN = randomSecret();
    private AccountSession() {}
    public static boolean isUnlocked() { return unlocked; }
    public static void unlock() { unlocked = true; }
    public static synchronized void unlockProfile(String id, boolean legacy) {
        ProfilePaths.validate(id);
        if (profileId != null && !profileId.equals(id)) throw new IllegalStateException("Restart before switching profiles");
        profileId = id; legacyProfile = legacy; unlocked = true;
    }
    public static String profileId() { return profileId; }
    public static boolean legacyProfile() { return legacyProfile; }
    public static String localToken() { return LOCAL_TOKEN; }
    public static String randomSecret() {
        byte[] bytes = new byte[32]; new SecureRandom().nextBytes(bytes);
        StringBuilder result = new StringBuilder();
        for (byte b : bytes) result.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return result.toString();
    }
}
