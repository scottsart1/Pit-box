package com.yourpitbox.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.text.InputFilter;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.inputmethod.InputMethodManager;
import android.content.Context;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.UUID;

/** Native email/PIN boundary. No secret is passed into dashboard JavaScript. */
public final class AccountGate extends ScrollView {
    private static final String API = "https://pitwall-activation.sarthakvij123450.workers.dev";
    private final Activity activity;
    private final KeyVault vault;
    private final Runnable onUnlocked;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final EditText email, pin, code;
    private final CheckBox sharing;
    private final TextView notice, heading;
    private final Button open, verify;
    private final String launchId = UUID.randomUUID().toString();
    private String challengeId, deviceSecret, boundAccount;
    private boolean busy, setup, ready;
    private int generation;

    public AccountGate(Activity activity, Runnable onUnlocked) {
        super(activity); this.activity = activity; this.onUnlocked = onUnlocked;
        vault = KeyVault.initialize(activity);
        setFillViewport(true); setBackgroundColor(Color.rgb(7, 11, 15));
        LinearLayout outer = new LinearLayout(activity);
        outer.setGravity(Gravity.CENTER); outer.setOrientation(LinearLayout.VERTICAL);
        outer.setPadding(dp(24), dp(40), dp(24), dp(40));
        addView(outer, new ScrollView.LayoutParams(-1, -1));
        LinearLayout card = new LinearLayout(activity);
        card.setOrientation(LinearLayout.VERTICAL); card.setPadding(dp(24), dp(24), dp(24), dp(24));
        GradientDrawable background = new GradientDrawable(); background.setColor(Color.rgb(18, 27, 36));
        background.setCornerRadius(dp(16)); background.setStroke(dp(1), Color.rgb(38, 53, 66)); card.setBackground(background);
        outer.addView(card, new LinearLayout.LayoutParams(Math.min(getResources().getDisplayMetrics().widthPixels - dp(48), dp(440)), -2));
        TextView brand = label("YOUR PIT BOX", 12); brand.setLetterSpacing(.16f); brand.setTextColor(Color.rgb(73, 209, 125)); card.addView(brand);
        heading = label("Unlock your pit box", 28); heading.setTypeface(null, Typeface.BOLD); card.addView(heading);
        card.addView(label("Your API keys stay encrypted on this device.", 14));
        email = field(card, "Email address", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS, 254);
        pin = field(card, "4-digit PIN", InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD, 4);
        code = field(card, "6-digit email code", InputType.TYPE_CLASS_NUMBER, 6); code.setVisibility(GONE);
        sharing = new CheckBox(activity); sharing.setText("Share account-linked app starts"); sharing.setTextSize(14);
        sharing.setTextColor(Color.rgb(220, 230, 238)); card.addView(sharing);
        card.addView(label("Optional: share launch time and app version under a random account ID, so we can measure returning users across devices. Kept for 180 days. No API keys, driving data or newsletter signup.", 12));
        notice = label("Internet is required to unlock. Your email verifies account ownership and recovery.", 13); card.addView(notice);
        open = button(card, "Unlock"); verify = button(card, "Set up account / reset PIN");
        open.setOnClickListener(v -> unlock()); verify.setOnClickListener(v -> challenge());
        // Keystore and preference IO run away from the UI thread.
        new Thread(() -> {
            try {
                deviceSecret = vault.read("account.device");
                if (deviceSecret == null) { deviceSecret = AccountSession.randomSecret(); vault.write("account.device", deviceSecret); }
                String savedEmail = vault.read("account.email"); boundAccount = vault.read("account.id");
                boolean consent = "true".equals(vault.read("account.share"));
                handler.post(() -> {
                    if (!alive()) return;
                    email.setText(savedEmail == null ? "" : savedEmail);
                    email.setEnabled(boundAccount == null); sharing.setChecked(consent); ready = true;
                });
            } catch (Exception failure) { handler.post(() -> notice.setText("The encrypted vault could not be opened. Your keys have not been changed.")); }
        }, "pitbox-vault").start();
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private TextView label(String value, int size) {
        TextView text = new TextView(activity); text.setText(value); text.setTextSize(size);
        text.setTextColor(Color.rgb(158, 178, 194)); text.setPadding(0, dp(8), 0, dp(8)); return text;
    }
    private EditText field(LinearLayout card, String hint, int type, int max) {
        EditText input = new EditText(activity); input.setHint(hint); input.setContentDescription(hint);
        input.setInputType(type); input.setSingleLine(true); input.setTextColor(Color.WHITE);
        input.setHintTextColor(Color.rgb(158, 178, 194)); input.setTextSize(17); input.setMinHeight(dp(52));
        input.setFilters(new InputFilter[]{new InputFilter.LengthFilter(max)});
        if (android.os.Build.VERSION.SDK_INT >= 26) input.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        input.setSaveEnabled(false); card.addView(input, new LinearLayout.LayoutParams(-1, -2)); return input;
    }
    private Button button(LinearLayout card, String title) {
        Button button = new Button(activity); button.setText(title); button.setAllCaps(false); button.setMinHeight(dp(48));
        card.addView(button, new LinearLayout.LayoutParams(-1, -2)); return button;
    }
    private boolean alive() { return !activity.isFinishing() && !activity.isDestroyed(); }
    private JSONObject base() throws Exception {
        return new JSONObject().put("email", email.getText().toString().trim().toLowerCase(Locale.ROOT)).put("device_secret", deviceSecret);
    }
    private void challenge() {
        if (busy || !ready) return;
        try {
            JSONObject data = base();
            submit("challenge", data, result -> {
                challengeId = result.getString("challenge_id"); setup = true;
                code.setVisibility(VISIBLE); open.setText("Verify and unlock"); heading.setText("Check your email");
                notice.setText("Enter the 6-digit email code and choose your 4-digit PIN. Use your existing PIN to add a device. Choosing a new PIN requires other devices to verify again.");
                verify.setText("Send a new code");
            });
        } catch (Exception invalid) { notice.setText("Enter your email address first."); }
    }
    private void unlock() {
        if (busy || !ready) return;
        if (!pin.getText().toString().matches("[0-9]{4}")) { notice.setText("Enter exactly four digits for your PIN."); return; }
        try {
            JSONObject data = base().put("pin", pin.getText().toString()).put("share_starts", sharing.isChecked())
                .put("launch_id", launchId).put("version", BuildConfig.VERSION_NAME);
            if (setup) data.put("challenge_id", challengeId).put("code", code.getText().toString());
            String address = email.getText().toString().trim().toLowerCase(Locale.ROOT);
            boolean consent = sharing.isChecked();
            submit(setup ? "verify" : "unlock", data, result -> {
                String account = result.getString("account_id");
                if (!account.matches("[0-9a-f-]{36}") || (boundAccount != null && !boundAccount.equals(account)))
                    throw new IllegalStateException("Account does not own this vault");
                // Persist identity before granting runtime access. No PIN or
                // reusable cloud session token is retained on disk.
                final int saveGeneration = generation;
                busy = true; open.setEnabled(false); verify.setEnabled(false);
                new Thread(() -> {
                    try {
                        vault.write("account.id", account); vault.write("account.email", address);
                        vault.write("account.share", String.valueOf(consent));
                        handler.post(() -> {
                            if (!alive() || saveGeneration != generation || getWindowVisibility() != VISIBLE) return;
                            pin.setText(""); code.setText("");
                            ((InputMethodManager) activity.getSystemService(Context.INPUT_METHOD_SERVICE))
                                .hideSoftInputFromWindow(getWindowToken(), 0);
                            AccountSession.unlock(); onUnlocked.run();
                        });
                    } catch (Exception error) { handler.post(() -> {
                        if (!alive() || saveGeneration != generation) return;
                        busy = false; open.setEnabled(true); verify.setEnabled(true);
                        notice.setText("Could not save the encrypted account. Please retry.");
                    }); }
                }, "pitbox-account-save").start();
            });
        } catch (Exception error) { notice.setText("Check the email, PIN and verification code."); }
    }
    private interface Success { void accept(JSONObject result) throws Exception; }
    private void submit(String route, JSONObject body, Success success) {
        busy = true; open.setEnabled(false); verify.setEnabled(false); email.setEnabled(false); pin.setEnabled(false); code.setEnabled(false);
        int requestGeneration = generation;
        notice.setText("Connecting securely…");
        new Thread(() -> {
            JSONObject result = null; String error = null; HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(API + "/account/" + route).openConnection();
                connection.setRequestMethod("POST"); connection.setConnectTimeout(10000); connection.setReadTimeout(15000);
                connection.setInstanceFollowRedirects(false); connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json");
                byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
                connection.setFixedLengthStreamingMode(bytes.length);
                try (java.io.OutputStream output = connection.getOutputStream()) { output.write(bytes); }
                int status = connection.getResponseCode();
                if (status != 200) {
                    error = status == 429 ? "Too many attempts. Wait 15 minutes; repeated failures may require a PIN reset."
                        : status == 401 ? "Details not accepted. Check your PIN, or verify this device by email."
                        : status == 400 ? "Check your email and code, then try again."
                        : "Account service unavailable. Your keys remain locked. Please retry shortly.";
                } else {
                    try (java.io.InputStream input = connection.getInputStream()) {
                        java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
                        byte[] buffer = new byte[1024]; int count;
                        while ((count = input.read(buffer)) != -1) { if (output.size() + count > 4096) throw new java.io.IOException(); output.write(buffer, 0, count); }
                        result = new JSONObject(output.toString("UTF-8"));
                    }
                }
            } catch (Exception unavailable) { error = "Can't connect. Internet is required to unlock; your keys remain encrypted."; }
            finally { if (connection != null) connection.disconnect(); }
            JSONObject response = result; String message = error;
            handler.post(() -> {
                if (!alive() || generation != requestGeneration) return;
                busy = false; open.setEnabled(true); verify.setEnabled(true); email.setEnabled(boundAccount == null);
                pin.setEnabled(true); code.setEnabled(true);
                if (message != null) { notice.setText(message); pin.setText(""); return; }
                try { success.accept(response); }
                catch (Exception invalid) { notice.setText("Could not unlock this device. Your keys have not been changed."); }
            });
        }, "pitbox-account").start();
    }
    @Override protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(visibility);
        if (visibility != VISIBLE && pin != null) {
            generation++; busy = false; pin.setText(""); code.setText("");
            open.setEnabled(true); verify.setEnabled(true); pin.setEnabled(true); code.setEnabled(true);
            email.setEnabled(boundAccount == null);
            notice.setText("Enter your PIN to continue. If a verification request was interrupted, request a new code.");
        }
    }
    @Override protected void onDetachedFromWindow() {
        generation++; pin.setText(""); code.setText(""); handler.removeCallbacksAndMessages(null);
        super.onDetachedFromWindow();
    }
}
