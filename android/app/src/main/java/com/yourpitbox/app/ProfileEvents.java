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

/**
 * Durable, pseudonymous successful sign-ins: a random profile ID, an event ID,
 * the time and the app version. No email, PIN, name, key or race data.
 */
public final class ProfileEvents {
    private static final String API = "https://pitwall-activation.sarthakvij123450.workers.dev/account/";
    /** Older events are dropped first; the server keeps 180 days anyway. */
    static final int MAX_PENDING = 500;
    private static final int BATCH = 50;
    private static boolean started;

    /** What a server answer means for a queued event. */
    enum Outcome { SENT, REJECTED, RETRY }

    /**
     * 200 is stored (or a duplicate). The answers the sign-in routes give a
     * request they will never accept (400 invalid, 401 wrong credential, 403,
     * 409 event conflict, 413, 422) would repeat on every retry, so that event
     * is dropped rather than blocking every later one. Everything else keeps
     * it queued: rate limits, outages, network errors (status -1), and 404 or
     * 405 from a service that does not have the sign-in routes yet.
     */
    static Outcome outcome(int status) {
        if (status == 200) return Outcome.SENT;
        switch (status) {
            case 400: case 401: case 403: case 409: case 413: case 422: return Outcome.REJECTED;
            default: return Outcome.RETRY;
        }
    }

    static final class Journal {
        private final ProfileStore.Storage storage;
        Journal(ProfileStore.Storage storage) { this.storage = storage; }
        JSONArray pending() throws Exception {
            String saved = storage.read("profiles.sign_ins");
            return saved == null ? new JSONArray() : new JSONArray(saved);
        }
        void enqueue(JSONObject profile, long epochSeconds, String version) throws Exception {
            JSONArray events = pending();
            events.put(new JSONObject().put("profile", profile.getString("id"))
                .put("event_id", UUID.randomUUID().toString()).put("occurred_at", epochSeconds)
                .put("version", version));
            JSONArray kept = new JSONArray();
            for (int i = Math.max(0, events.length() - MAX_PENDING); i < events.length(); i++) kept.put(events.get(i));
            storage.write("profiles.sign_ins", kept.toString());
        }
        /** Entries this app never writes; removed so they cannot stall the queue. */
        int dropMalformed() throws Exception {
            JSONArray kept = new JSONArray(), events = pending();
            for (int i = 0; i < events.length(); i++) {
                JSONObject event = events.optJSONObject(i);
                if (event != null && !event.optString("event_id").isEmpty()) kept.put(event);
            }
            int dropped = events.length() - kept.length();
            if (dropped > 0) storage.write("profiles.sign_ins", kept.toString());
            return dropped;
        }
        void acknowledged(String id) throws Exception {
            JSONArray kept = new JSONArray(), events = pending();
            for (int i = 0; i < events.length(); i++) if (!id.equals(events.getJSONObject(i).optString("event_id"))) kept.put(events.getJSONObject(i));
            storage.write("profiles.sign_ins", kept.toString());
        }
    }

    interface Transport { int post(String route, JSONObject body); }

    /**
     * Sends up to one batch. Stops at the first retryable failure so a
     * network outage costs one request per minute, not one per event.
     * Returns the number of events removed from the queue.
     */
    static int flush(Journal journal, ProfileStore store, Transport transport) throws Exception {
        JSONArray events = journal.pending();
        int removed = journal.dropMalformed();
        for (int i = 0; i < Math.min(events.length(), BATCH); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null || event.optString("event_id").isEmpty()) continue;
            JSONObject profile;
            try { profile = store.find(event.optString("profile")); }
            catch (ProfileStore.Rejected unknown) { profile = null; }
            Outcome result = Outcome.REJECTED;
            if (profile != null) {
                result = outcome(transport.post("profile", registration(profile)));
                if (result == Outcome.SENT) result = outcome(transport.post("sign-in", payload(profile, event)));
            }
            if (result == Outcome.RETRY) break;
            journal.acknowledged(event.getString("event_id"));
            removed++;
        }
        return removed;
    }

    private static Journal journal(Context c) {
        KeyVault vault = KeyVault.initialize(c);
        return new Journal(new ProfileStore.Storage() {
            public String read(String key) throws Exception { return vault.read(key); }
            public void write(String key, String value) throws Exception { vault.write(key, value); }
        });
    }
    public static synchronized void enqueue(Context c, JSONObject profile) throws Exception {
        journal(c).enqueue(profile, System.currentTimeMillis() / 1000, BuildConfig.VERSION_NAME);
    }
    static JSONObject registration(JSONObject profile) throws Exception {
        return new JSONObject().put("account_id", profile.getString("account")).put("device_secret", profile.getString("device"));
    }
    static JSONObject payload(JSONObject profile, JSONObject event) throws Exception {
        return registration(profile).put("event_id", event.getString("event_id"))
            .put("occurred_at", event.getLong("occurred_at")).put("version", event.getString("version"));
    }
    /**
     * Sends now, then every minute while this process lives. Debug builds
     * (development and the CI emulator) queue but never send, so the owner
     * report counts only installed releases and QA builds.
     */
    public static synchronized void start(Context context) {
        if (started || BuildConfig.DEBUG) return; started = true;
        Context app = context.getApplicationContext();
        Executors.newSingleThreadScheduledExecutor(r -> { Thread t = new Thread(r, "pitbox-sign-ins"); t.setDaemon(true); return t; })
            .scheduleWithFixedDelay(() -> {
                try {
                    synchronized (ProfileEvents.class) { flush(journal(app), new ProfileStore(app), ProfileEvents::post); }
                } catch (Exception unavailable) { /* Keep queued; retry without exposing request contents. */ }
            }, 0, 60, TimeUnit.SECONDS);
    }
    private static int post(String route, JSONObject body) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(API + route).openConnection();
            connection.setRequestMethod("POST"); connection.setConnectTimeout(10000); connection.setReadTimeout(10000);
            connection.setInstanceFollowRedirects(false); connection.setDoOutput(true); connection.setUseCaches(false);
            connection.setRequestProperty("Content-Type", "application/json");
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8); connection.setFixedLengthStreamingMode(bytes.length);
            try (java.io.OutputStream out = connection.getOutputStream()) { out.write(bytes); }
            return connection.getResponseCode();
        } catch (Exception offline) {
            return -1;
        } finally { if (connection != null) connection.disconnect(); }
    }
}
