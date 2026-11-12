package com.yourpitbox.app;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** Durable, pseudonymous successful sign-ins. No email, PIN, label or race data. */
public final class ProfileEvents {
    private static final String API = "https://pitwall-activation.sarthakvij123450.workers.dev/account/";
    private static boolean started;
    static final class Journal {
        private final ProfileStore.Storage storage;
        Journal(ProfileStore.Storage storage) { this.storage = storage; }
        JSONArray pending() throws Exception {
            String saved = storage.read("profiles.sign_ins");
            return saved == null ? new JSONArray() : new JSONArray(saved);
        }
        void enqueue(JSONObject profile) throws Exception {
            JSONArray events = pending();
            events.put(new JSONObject().put("profile", profile.getString("id"))
                .put("event_id", UUID.randomUUID().toString()).put("occurred_at", System.currentTimeMillis() / 1000)
                .put("version", BuildConfig.VERSION_NAME));
            storage.write("profiles.sign_ins", events.toString());
        }
        void acknowledged(String id) throws Exception {
            JSONArray kept = new JSONArray(), events = pending();
            for (int i = 0; i < events.length(); i++) if (!id.equals(events.getJSONObject(i).getString("event_id"))) kept.put(events.getJSONObject(i));
            storage.write("profiles.sign_ins", kept.toString());
        }
    }
    private static Journal journal(Context c) {
        KeyVault vault = KeyVault.initialize(c);
        return new Journal(new ProfileStore.Storage() {
            public String read(String key) throws Exception { return vault.read(key); }
            public void write(String key, String value) throws Exception { vault.write(key, value); }
        });
    }
    private static synchronized JSONArray pending(Context c) throws Exception { return journal(c).pending(); }
    public static synchronized void enqueue(Context c, JSONObject profile) throws Exception { journal(c).enqueue(profile); }
    private static synchronized void acknowledged(Context c, String id) throws Exception { journal(c).acknowledged(id); }
    static JSONObject registration(JSONObject profile) throws Exception {
        return new JSONObject().put("account_id", profile.getString("account")).put("device_secret", profile.getString("device"));
    }
    static JSONObject payload(JSONObject profile, JSONObject event) throws Exception {
        return registration(profile).put("event_id", event.getString("event_id"))
            .put("occurred_at", event.getLong("occurred_at")).put("version", event.getString("version"));
    }
    public static synchronized void start(Context context) {
        if (started) return; started = true;
        Context app = context.getApplicationContext();
        Executors.newSingleThreadScheduledExecutor(r -> { Thread t = new Thread(r, "pitbox-sign-ins"); t.setDaemon(true); return t; })
            .scheduleWithFixedDelay(() -> flush(app), 0, 60, TimeUnit.SECONDS);
    }
    private static void flush(Context c) {
        try {
            JSONArray events = pending(c); ProfileStore store = new ProfileStore(c);
            for (int i = 0; i < Math.min(events.length(), 50); i++) {
                JSONObject event = events.getJSONObject(i), profile = store.find(event.getString("profile"));
                if (!post("profile", registration(profile))) return;
                if (!post("sign-in", payload(profile, event))) return;
                acknowledged(c, event.getString("event_id"));
            }
        } catch (Exception unavailable) { /* Keep queued; retry without exposing request contents. */ }
    }
    private static boolean post(String route, JSONObject body) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(API + route).openConnection();
        try {
            connection.setRequestMethod("POST"); connection.setConnectTimeout(10000); connection.setReadTimeout(10000);
            connection.setInstanceFollowRedirects(false); connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json");
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8); connection.setFixedLengthStreamingMode(bytes.length);
            try (java.io.OutputStream out = connection.getOutputStream()) { out.write(bytes); }
            return connection.getResponseCode() == 200;
        } finally { connection.disconnect(); }
    }
}
