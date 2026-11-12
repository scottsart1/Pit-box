package com.yourpitbox.app;

import java.io.File;

/** Shared namespace policy for profile workspaces, preferences, WebView data and secrets. */
public final class ProfilePaths {
    private ProfilePaths() {}
    public static void validate(String id) {
        if (id == null || !id.matches("[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"))
            throw new IllegalArgumentException("Invalid profile identifier");
    }
    public static File directory(File files, String id, boolean legacy) {
        if (id == null) return files;
        validate(id);
        return legacy ? files : new File(files, "profiles/" + id);
    }
    public static String preferenceFile(String id, boolean legacy) {
        if (id == null) return "driver_dashboard";
        validate(id);
        return legacy ? "driver_dashboard" : "driver_dashboard_" + id;
    }
    public static String providerName(String id, boolean legacy, String name) {
        if (id == null) return name;
        validate(id);
        return legacy ? name : "profile." + id + ".key." + name;
    }
    /**
     * WebView keeps local storage, cookies and cache per data directory. The
     * first profile keeps the existing directory (and with it the dashboard
     * layout already saved there); every other driver gets its own.
     */
    public static String webViewSuffix(String id, boolean legacy) {
        if (id == null) return null;
        validate(id);
        return legacy ? null : "driver-" + id;
    }
    /**
     * True when this installation was used before profiles existed, so the
     * first profile is told that it inherits that history and those settings.
     * Nothing under files/ is written before a profile is opened.
     */
    public static boolean hasExistingData(File files) {
        File data = new File(files, "PitWallData");
        String[] entries = data.list();
        return (entries != null && entries.length > 0) || new File(files, ".env").isFile();
    }
}
