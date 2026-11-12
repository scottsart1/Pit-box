package com.yourpitbox.app;

import org.json.JSONObject;
import java.io.File;
import java.nio.file.Files;
import java.util.HashMap;
import java.util.Map;

/** Runs on the JVM; the injected store replaces only Android encrypted persistence. */
public final class ProfileStoreCheck {
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
    private interface Action { void run() throws Exception; }
    private static void rejected(Action a) throws Exception {
        try { a.run(); throw new AssertionError("Expected rejection"); } catch (IllegalArgumentException expected) {}
    }
    public static void main(String[] args) throws Exception {
        Map<String,String> saved = new HashMap<>();
        ProfileStore.Storage memory = new ProfileStore.Storage() {
            public String read(String key) { return saved.get(key); }
            public void write(String key,String value) { saved.put(key,value); }
        };
        ProfileStore store = new ProfileStore(memory);
        JSONObject first = store.create("Driver One", "pin", "", "0123", null, AccountSession.randomSecret());
        JSONObject second = store.create("Driver Two", "pin", "", "9876", null, AccountSession.randomSecret());
        JSONObject email = store.create("Email Driver", "email", "driver@example.test", "", null, AccountSession.randomSecret());
        String a=first.getString("id"), b=second.getString("id");
        check(first.getBoolean("legacy")); check(!second.getBoolean("legacy"));
        check(!saved.get("profiles.index").contains("0123"));
        check(store.checkPin(a,"0123")); check(!store.checkPin(b,"0123")); check(store.checkPin(b,"9876"));
        check(!store.checkPin(email.getString("id"),"0000"));
        rejected(() -> store.create("DRIVER ONE","pin","","1111",null,"x"));
        rejected(() -> store.create("Duplicate email","email","driver@example.test","",null,"x"));
        rejected(() -> PinCredential.derive("123","salt"));
        rejected(() -> PinCredential.derive("１２３４","salt"));
        check(!PinCredential.derive("0123","salt1").equals(PinCredential.derive("0123","salt2")));
        for(int i=0;i<5;i++) check(!store.checkPin(a,"9999"));
        ProfileStore reopened = new ProfileStore(memory);
        rejected(() -> reopened.checkPin(a,"0123")); // Lock survives reopening the store.
        JSONObject blocked = reopened.find(a); blocked.put("blocked_until",0); reopened.save(blocked);
        check(reopened.checkPin(a,"0123"));
        JSONObject daily = reopened.find(a); daily.put("daily_failures",20); reopened.save(daily);
        rejected(() -> reopened.checkPin(a,"0123"));
        File root=Files.createTempDirectory("pitbox-profile-check").toFile();
        check(ProfilePaths.directory(root,a,true).equals(root));
        File secondRoot=ProfilePaths.directory(root,b,false);check(!secondRoot.equals(root));
        check(!secondRoot.equals(ProfilePaths.directory(root,email.getString("id"),false)));
        check(ProfilePaths.providerName(a,true,"OPENAI_API_KEY").equals("OPENAI_API_KEY"));
        check(!ProfilePaths.providerName(b,false,"OPENAI_API_KEY").equals("OPENAI_API_KEY"));
        check(!ProfilePaths.providerName(b,false,"OPENAI_API_KEY").equals(ProfilePaths.providerName(email.getString("id"),false,"OPENAI_API_KEY")));
        check(ProfilePaths.preferenceFile(a,true).equals("driver_dashboard"));
        check(!ProfilePaths.preferenceFile(b,false).equals("driver_dashboard"));
        check(!ProfilePaths.preferenceFile(b,false).equals(ProfilePaths.preferenceFile(email.getString("id"),false)));
        rejected(() -> ProfilePaths.directory(root,"../../another",false));
        ProfileEvents.Journal journal = new ProfileEvents.Journal(memory);
        journal.enqueue(first); journal.enqueue(email);
        ProfileEvents.Journal reloadedJournal = new ProfileEvents.Journal(memory);
        check(reloadedJournal.pending().length() == 2);
        JSONObject event = reloadedJournal.pending().getJSONObject(1);
        JSONObject report = ProfileEvents.payload(email, event);
        check(report.length() == 5);
        check(!report.toString().contains("driver@example.test"));
        check(!report.has("pin") && !report.has("email") && !report.has("name"));
        String firstEvent = reloadedJournal.pending().getJSONObject(0).getString("event_id");
        reloadedJournal.acknowledged(firstEvent);
        check(reloadedJournal.pending().length() == 1);
        reloadedJournal.acknowledged(firstEvent); // Repeated acknowledgement is harmless.
        check(reloadedJournal.pending().length() == 1);
        check(reloadedJournal.pending().getJSONObject(0).getString("event_id").equals(event.getString("event_id")));
        String sha1 = PinCredential.derive("0123", "legacy-salt", "PBKDF2WithHmacSHA1");
        check(PinCredential.matches("0123", "legacy-salt", "PBKDF2WithHmacSHA1", sha1));
        AccountSession.unlockProfile(a,true);check(AccountSession.isUnlocked());
        try { AccountSession.unlockProfile(b,false);throw new AssertionError("Changed a running profile"); } catch(IllegalStateException expected) {}
        check(AccountSession.profileId().equals(a));
        System.out.println("Profile credential, restart lockout, namespace, durable event queue and process-boundary checks passed");
    }
}
