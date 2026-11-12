package com.yourpitbox.app;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.util.UUID;

/** Encrypted profile index. Only the first profile can inherit pre-profile data. */
public final class ProfileStore {
    interface Storage { String read(String key) throws Exception; void write(String key, String value) throws Exception; }
    private final Storage vault;
    public ProfileStore(Context context) {
        KeyVault keys = KeyVault.initialize(context);
        vault = new Storage() {
            public String read(String key) throws Exception { return keys.read(key); }
            public void write(String key, String value) throws Exception { keys.write(key, value); }
        };
    }
    ProfileStore(Storage storage) { vault = storage; }
    public synchronized JSONArray list() throws Exception {
        String data = vault.read("profiles.index");
        if (data == null && vault.read("account.id") != null)
            throw new IllegalStateException("An earlier account preview needs an authenticated migration");
        return data == null ? new JSONArray() : new JSONArray(data);
    }
    public synchronized JSONObject find(String id) throws Exception {
        JSONArray profiles = list();
        for (int i = 0; i < profiles.length(); i++) {
            JSONObject profile = profiles.getJSONObject(i);
            if (profile.getString("id").equals(id)) return profile;
        }
        throw new IllegalArgumentException("Unknown profile");
    }
    public synchronized JSONObject create(String name, String method, String email, String pin,
                                          String account, String device) throws Exception {
        JSONArray profiles = list();
        if (name.trim().isEmpty() || name.length() > 40 || profiles.length() >= 20)
            throw new IllegalArgumentException("Choose a profile name (up to 40 characters). Maximum 20 profiles.");
        for (int i = 0; i < profiles.length(); i++) {
            JSONObject p = profiles.getJSONObject(i);
            if (p.getString("name").equalsIgnoreCase(name.trim())) throw new IllegalArgumentException("That profile name is already in use.");
            if (!email.isEmpty() && email.equals(p.optString("email"))) throw new IllegalArgumentException("Choose your existing email profile.");
        }
        String id = UUID.randomUUID().toString();
        JSONObject profile = new JSONObject().put("id", id).put("name", name.trim())
            .put("method", method).put("email", email).put("device", device)
            .put("account", account == null ? id : account).put("legacy", profiles.length() == 0);
        if ("pin".equals(method)) {
            String salt = AccountSession.randomSecret();
            String algorithm = PinCredential.algorithm();
            profile.put("salt", salt).put("algorithm", algorithm).put("verifier", PinCredential.derive(pin, salt, algorithm));
        }
        profiles.put(profile); vault.write("profiles.index", profiles.toString());
        return profile;
    }
    public synchronized boolean checkPin(String id, String pin) throws Exception {
        JSONObject p = find(id); long now = System.currentTimeMillis();
        if (!"pin".equals(p.getString("method"))) return false;
        if (now < p.optLong("blocked_until")) throw new IllegalArgumentException("Too many PIN attempts. Try again in 15 minutes.");
        long day = now / 86400000L;
        if (p.optLong("day", -1) != day) p.put("day", day).put("daily_failures", 0);
        if (p.optInt("daily_failures") >= 20) throw new IllegalArgumentException("PIN attempts are locked until tomorrow.");
        // Reserve persistently before expensive verification. Crashes cannot reset guesses.
        int failures = p.optInt("failures") + 1;
        p.put("failures", failures).put("daily_failures", p.optInt("daily_failures") + 1);
        if (failures >= 5) p.put("blocked_until", now + 900000L).put("failures", 0);
        save(p);
        boolean valid = PinCredential.matches(pin, p.getString("salt"), p.getString("algorithm"), p.getString("verifier"));
        if (valid) { p.put("failures", 0).put("blocked_until", 0).put("daily_failures", p.optInt("daily_failures") - 1); save(p); }
        return valid;
    }
    public synchronized void save(JSONObject profile) throws Exception {
        JSONArray profiles = list();
        for (int i = 0; i < profiles.length(); i++) if (profiles.getJSONObject(i).getString("id").equals(profile.getString("id"))) {
            profiles.put(i, profile); vault.write("profiles.index", profiles.toString()); return;
        }
        throw new IllegalArgumentException("Unknown profile");
    }
    public static File directory(Context context) {
        File root = ProfilePaths.directory(context.getFilesDir(), AccountSession.profileId(), AccountSession.legacyProfile());
        if (!root.isDirectory() && !root.mkdirs()) throw new IllegalStateException("Profile storage unavailable");
        return root;
    }
}
