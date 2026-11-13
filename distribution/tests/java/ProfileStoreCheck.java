package com.yourpitbox.app;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Runs on the JVM; the injected store replaces only Android encrypted persistence. */
public final class ProfileStoreCheck {
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
    private interface Action { void run() throws Exception; }
    private static void rejected(Action a) throws Exception {
        try { a.run(); throw new AssertionError("Expected rejection"); } catch (IllegalArgumentException expected) {}
    }
    private static void rejected(ProfileStore.Reason reason, Action a) throws Exception {
        try { a.run(); throw new AssertionError("Expected " + reason); }
        catch (ProfileStore.Rejected expected) { check(expected.reason == reason); }
    }
    private static ProfileStore.Storage memory(Map<String, String> saved) {
        return new ProfileStore.Storage() {
            public String read(String key) { return saved.get(key); }
            public void write(String key, String value) { saved.put(key, value); }
        };
    }

    public static void main(String[] args) throws Exception {
        credentialsAndLockouts();
        namespaces();
        upgradeDetection();
        signInQueue();
        deliveryOutcomes();
        processBoundary();
        System.out.println("Profile credential, lockout, namespace, upgrade, sign-in queue and process-boundary checks passed");
    }

    private static void credentialsAndLockouts() throws Exception {
        Map<String,String> saved = new HashMap<>();
        ProfileStore store = new ProfileStore(memory(saved));
        check(store.list().length() == 0);
        JSONObject first = store.create("Driver One", "pin", "", "0123", null, AccountSession.randomSecret());
        JSONObject second = store.create("Driver Two", "pin", "", "9876", null, AccountSession.randomSecret());
        JSONObject email = store.create("Email Driver", "email", "driver@example.test", "", null, AccountSession.randomSecret());
        String a=first.getString("id"), b=second.getString("id"), e=email.getString("id");
        // Only the first profile inherits data stored before profiles existed.
        check(first.getBoolean("legacy")); check(!second.getBoolean("legacy")); check(!email.getBoolean("legacy"));
        check(!saved.get("profiles.index").contains("0123"));
        check(first.getString("email").isEmpty());
        check(store.checkPin(a,"0123")); check(!store.checkPin(b,"0123")); check(store.checkPin(b,"9876"));
        check(!store.checkPin(e,"0000"));
        check(store.checkEmail(e,"driver@example.test")); check(!store.checkEmail(e,"other@example.test"));
        check(!store.checkEmail(a,""));
        rejected(ProfileStore.Reason.NAME_TAKEN, () -> store.create("DRIVER ONE","pin","","1111",null,"x"));
        rejected(ProfileStore.Reason.EMAIL_TAKEN, () -> store.create("Duplicate email","email","driver@example.test","",null,"x"));
        rejected(ProfileStore.Reason.NAME, () -> store.create("   ","pin","","1111",null,"x"));
        rejected(ProfileStore.Reason.NAME, () -> store.create("x".repeat(41),"pin","","1111",null,"x"));
        rejected(ProfileStore.Reason.UNKNOWN_PROFILE, () -> store.find("12345678-1234-4123-8123-123456789abc"));
        rejected(() -> PinCredential.derive("123","salt"));
        rejected(() -> PinCredential.derive("１２３４","salt"));
        check(!PinCredential.derive("0123","salt1").equals(PinCredential.derive("0123","salt2")));
        for(int i=0;i<5;i++) check(!store.checkPin(a,"9999"));
        ProfileStore reopened = new ProfileStore(memory(saved));
        rejected(ProfileStore.Reason.LOCKED, () -> reopened.checkPin(a,"0123")); // Lock survives reopening the store.
        JSONObject blocked = reopened.find(a); blocked.put("blocked_until",0); reopened.save(blocked);
        check(reopened.checkPin(a,"0123"));
        JSONObject daily = reopened.find(a); daily.put("daily_failures",20); reopened.save(daily);
        rejected(ProfileStore.Reason.LOCKED_DAY, () -> reopened.checkPin(a,"0123"));
        String sha1 = PinCredential.derive("0123", "legacy-salt", "PBKDF2WithHmacSHA1");
        check(PinCredential.matches("0123", "legacy-salt", "PBKDF2WithHmacSHA1", sha1));

        Map<String,String> full = new HashMap<>();
        ProfileStore crowded = new ProfileStore(memory(full));
        for (int i = 0; i < ProfileStore.MAX_PROFILES; i++) crowded.create("Driver " + i, "pin", "", "1234", null, "x");
        rejected(ProfileStore.Reason.LIMIT, () -> crowded.create("One more", "pin", "", "1234", null, "x"));
    }

    private static void namespaces() throws Exception {
        String a = "12345678-1234-4123-8123-123456789abc", b = "87654321-4321-4321-8321-cba987654321";
        File root=Files.createTempDirectory("pitbox-profile-check").toFile();
        check(ProfilePaths.directory(root,a,true).equals(root));
        File secondRoot=ProfilePaths.directory(root,b,false);check(!secondRoot.equals(root));
        check(secondRoot.getParentFile().getParentFile().equals(root));
        check(ProfilePaths.providerName(a,true,"OPENAI_API_KEY").equals("OPENAI_API_KEY"));
        check(!ProfilePaths.providerName(b,false,"OPENAI_API_KEY").equals("OPENAI_API_KEY"));
        check(ProfilePaths.preferenceFile(a,true).equals("driver_dashboard"));
        check(!ProfilePaths.preferenceFile(b,false).equals("driver_dashboard"));
        // The first profile keeps the WebView data (dashboard layout, tour) it had.
        check(ProfilePaths.webViewSuffix(a,true) == null);
        check(ProfilePaths.webViewSuffix(b,false).equals("driver-" + b));
        check(ProfilePaths.webViewSuffix(null,false) == null);
        rejected(() -> ProfilePaths.directory(root,"../../another",false));
        rejected(() -> ProfilePaths.webViewSuffix("../another",false));
    }

    private static void upgradeDetection() throws Exception {
        File fresh=Files.createTempDirectory("pitbox-fresh").toFile();
        check(!ProfilePaths.hasExistingData(fresh));
        new File(fresh, "PitWallData").mkdirs();
        check(!ProfilePaths.hasExistingData(fresh)); // An empty data folder holds nothing to inherit.
        Files.write(new File(fresh, "PitWallData/pitwall.db").toPath(), new byte[]{1});
        check(ProfilePaths.hasExistingData(fresh));
        File keysOnly=Files.createTempDirectory("pitbox-keys").toFile();
        Files.write(new File(keysOnly, ".env").toPath(), "OPENAI_API_KEY=x\n".getBytes(java.nio.charset.StandardCharsets.UTF_8));
        check(ProfilePaths.hasExistingData(keysOnly));
    }

    private static void signInQueue() throws Exception {
        Map<String,String> saved = new HashMap<>();
        ProfileStore store = new ProfileStore(memory(saved));
        JSONObject first = store.create("Driver One", "pin", "", "0123", null, AccountSession.randomSecret());
        JSONObject email = store.create("Email Driver", "email", "driver@example.test", "", null, AccountSession.randomSecret());
        ProfileEvents.Journal journal = new ProfileEvents.Journal(memory(saved));
        journal.enqueue(first, 1700000000L, "5.7.1-android.43"); journal.enqueue(email, 1700000100L, "5.7.1-android.43");
        ProfileEvents.Journal reloadedJournal = new ProfileEvents.Journal(memory(saved));
        check(reloadedJournal.pending().length() == 2);
        JSONObject event = reloadedJournal.pending().getJSONObject(1);
        JSONObject report = ProfileEvents.payload(email, event);
        check(report.length() == 5);
        check(!report.toString().contains("driver@example.test"));
        check(!report.toString().contains("Email Driver"));
        check(!report.has("pin") && !report.has("email") && !report.has("name"));
        check(report.getLong("occurred_at") == 1700000100L);
        String firstEvent = reloadedJournal.pending().getJSONObject(0).getString("event_id");
        reloadedJournal.acknowledged(firstEvent);
        check(reloadedJournal.pending().length() == 1);
        reloadedJournal.acknowledged(firstEvent); // Repeated acknowledgement is harmless.
        check(reloadedJournal.pending().length() == 1);
        check(reloadedJournal.pending().getJSONObject(0).getString("event_id").equals(event.getString("event_id")));

        // A device that stays offline keeps the newest events, bounded.
        Map<String,String> offline = new HashMap<>();
        ProfileEvents.Journal bounded = new ProfileEvents.Journal(memory(offline));
        for (int i = 0; i < ProfileEvents.MAX_PENDING + 25; i++) bounded.enqueue(first, i, "v");
        JSONArray kept = bounded.pending();
        check(kept.length() == ProfileEvents.MAX_PENDING);
        check(kept.getJSONObject(0).getLong("occurred_at") == 25);
        check(kept.getJSONObject(kept.length() - 1).getLong("occurred_at") == ProfileEvents.MAX_PENDING + 24);
    }

    private static void deliveryOutcomes() throws Exception {
        check(ProfileEvents.outcome(200) == ProfileEvents.Outcome.SENT);
        for (int status : new int[]{400, 401, 403, 409, 413, 422}) check(ProfileEvents.outcome(status) == ProfileEvents.Outcome.REJECTED);
        // 404/405: a service without the sign-in routes yet. Keep the record.
        for (int status : new int[]{-1, 404, 405, 408, 429, 500, 502, 503, 302}) check(ProfileEvents.outcome(status) == ProfileEvents.Outcome.RETRY);

        Map<String,String> saved = new HashMap<>();
        ProfileStore store = new ProfileStore(memory(saved));
        JSONObject driver = store.create("Driver", "pin", "", "0123", null, AccountSession.randomSecret());
        ProfileEvents.Journal journal = new ProfileEvents.Journal(memory(saved));
        for (int i = 0; i < 3; i++) journal.enqueue(driver, 1700000000L + i, "v");

        // Offline: nothing is lost, and one attempt is made rather than one per event.
        List<String> calls = new ArrayList<>();
        check(ProfileEvents.flush(journal, store, (route, body) -> { calls.add(route); return -1; }) == 0);
        check(calls.size() == 1 && journal.pending().length() == 3);

        // A request the server rejects is dropped instead of blocking the rest.
        int[] signIns = {0};
        int removed = ProfileEvents.flush(journal, store, (route, body) -> {
            if (route.equals("profile")) return 200;
            return signIns[0]++ == 0 ? 400 : 200;
        });
        check(removed == 3 && journal.pending().length() == 0);

        // A queued event whose profile no longer exists cannot stall the queue.
        journal.enqueue(new JSONObject().put("id", "12345678-1234-4123-8123-123456789abc"), 1L, "v");
        journal.enqueue(driver, 2L, "v");
        List<String> sent = new ArrayList<>();
        check(ProfileEvents.flush(journal, store, (route, body) -> { sent.add(body.toString()); return 200; }) == 2);
        check(sent.size() == 2 && journal.pending().length() == 0);

        // Rate limited mid-batch: the delivered event is acknowledged, the rest wait.
        for (int i = 0; i < 3; i++) journal.enqueue(driver, 10L + i, "v");
        int[] answered = {0};
        check(ProfileEvents.flush(journal, store, (route, body) -> answered[0]++ < 2 ? 200 : 429) == 1);
        check(journal.pending().length() == 2);

        // Entries this app never writes are removed.
        saved.put("profiles.sign_ins", new JSONArray().put("junk").put(new JSONObject()).toString());
        check(ProfileEvents.flush(journal, store, (route, body) -> 200) == 2);
        check(journal.pending().length() == 0);
    }

    private static void processBoundary() {
        String a = "12345678-1234-4123-8123-123456789abc", b = "87654321-4321-4321-8321-cba987654321";
        check(!AccountSession.isUnlocked());
        AccountSession.unlockProfile(a, "Driver One", true); check(AccountSession.isUnlocked());
        check(AccountSession.profileName().equals("Driver One"));
        AccountSession.unlockProfile(a, "Driver One", true); // Reopening the same profile is harmless.
        try { AccountSession.unlockProfile(b, "Driver Two", false); throw new AssertionError("Changed a running profile"); }
        catch (IllegalStateException expected) {}
        check(AccountSession.profileId().equals(a) && AccountSession.legacyProfile());
    }
}
