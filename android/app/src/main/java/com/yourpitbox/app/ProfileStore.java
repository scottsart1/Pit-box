package com.yourpitbox.app;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;

/** Encrypted profile index. Only the first profile can inherit pre-profile data. */
public final class ProfileStore {
    interface Storage { String read(String key) throws Exception; void write(String key, String value) throws Exception; }

    static final int MAX_PROFILES = 20;
    static final int MAX_NAME = 40;
    static final long LOCKOUT_MS = 15 * 60 * 1000L;
    static final int FAILURES_BEFORE_LOCKOUT = 5;
    static final int DAILY_FAILURE_LIMIT = 20;

    /** Why a profile could not be created or opened; the screen words it. */
    enum Reason { NAME, NAME_TAKEN, EMAIL_TAKEN, LIMIT, UNKNOWN_PROFILE, WRONG_EMAIL, WRONG_PIN, LOCKED, LOCKED_DAY }

    static final class Rejected extends IllegalArgumentException {
        final Reason reason;
        Rejected(Reason reason) { super(reason.name()); this.reason = reason; }
    }

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
        return data == null ? new JSONArray() : new JSONArray(data);
    }

    public synchronized JSONObject find(String id) throws Exception {
        JSONArray profiles = list();
        for (int i = 0; i < profiles.length(); i++) {
            JSONObject profile = profiles.getJSONObject(i);
            if (profile.getString("id").equals(id)) return profile;
        }
        throw new Rejected(Reason.UNKNOWN_PROFILE);
    }

    public synchronized JSONObject create(String name, String method, String email, String pin,
                                          String account, String device) throws Exception {
        JSONArray profiles = list();
        String label = name == null ? "" : name.trim();
        if (label.isEmpty() || label.length() > MAX_NAME) throw new Rejected(Reason.NAME);
        if (profiles.length() >= MAX_PROFILES) throw new Rejected(Reason.LIMIT);
        if (!"pin".equals(method) && !"email".equals(method)) throw new IllegalArgumentException("Unknown sign-in method");
        for (int i = 0; i < profiles.length(); i++) {
            JSONObject p = profiles.getJSONObject(i);
            if (p.getString("name").equalsIgnoreCase(label)) throw new Rejected(Reason.NAME_TAKEN);
            if (!email.isEmpty() && email.equals(p.optString("email"))) throw new Rejected(Reason.EMAIL_TAKEN);
        }
        String id = UUID.randomUUID().toString();
        // The first profile owns everything stored before profiles existed.
        JSONObject profile = new JSONObject().put("id", id).put("name", label)
            .put("method", method).put("email", "email".equals(method) ? email : "").put("device", device)
            .put("account", account == null ? id : account).put("legacy", profiles.length() == 0);
        if ("pin".equals(method)) {
            String salt = AccountSession.randomSecret();
            String algorithm = PinCredential.algorithm();
            profile.put("salt", salt).put("algorithm", algorithm).put("verifier", PinCredential.derive(pin, salt, algorithm));
        }
        profiles.put(profile); vault.write("profiles.index", profiles.toString());
        return profile;
    }

    /** Email profiles: the address is an identifier, compared in constant time. */
    public synchronized boolean checkEmail(String id, String address) throws Exception {
        JSONObject p = find(id);
        if (!"email".equals(p.getString("method"))) return false;
        return MessageDigest.isEqual(address.getBytes(StandardCharsets.UTF_8),
            p.getString("email").getBytes(StandardCharsets.UTF_8));
    }

    public synchronized boolean checkPin(String id, String pin) throws Exception {
        JSONObject p = find(id); long now = System.currentTimeMillis();
        if (!"pin".equals(p.getString("method"))) return false;
        if (now < p.optLong("blocked_until")) throw new Rejected(Reason.LOCKED);
        long day = now / 86400000L;
        if (p.optLong("day", -1) != day) p.put("day", day).put("daily_failures", 0);
        if (p.optInt("daily_failures") >= DAILY_FAILURE_LIMIT) throw new Rejected(Reason.LOCKED_DAY);
        // Reserve persistently before expensive verification. Crashes cannot reset guesses.
        int failures = p.optInt("failures") + 1;
        p.put("failures", failures).put("daily_failures", p.optInt("daily_failures") + 1);
        if (failures >= FAILURES_BEFORE_LOCKOUT) p.put("blocked_until", now + LOCKOUT_MS).put("failures", 0);
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
        throw new Rejected(Reason.UNKNOWN_PROFILE);
    }

    public static File directory(Context context) {
        File root = ProfilePaths.directory(context.getFilesDir(), AccountSession.profileId(), AccountSession.legacyProfile());
        if (!root.isDirectory() && !root.mkdirs()) throw new IllegalStateException("Profile storage unavailable");
        return root;
    }
}
