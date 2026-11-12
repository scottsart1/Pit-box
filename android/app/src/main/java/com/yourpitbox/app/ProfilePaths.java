package com.yourpitbox.app;

import java.io.File;

/** Shared namespace policy for profile workspaces and provider secrets. */
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
    public static String providerName(String id, boolean legacy, String name) {
        if (id == null) return name;
        validate(id);
        return legacy ? name : "profile." + id + ".key." + name;
    }
}
