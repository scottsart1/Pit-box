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
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.Locale;

/** Local profiles: email is an identifier; a PIN is an additional app lock. */
public final class AccountGate extends ScrollView {
    private final Activity activity;
    private final ProfileStore profiles;
    private final Runnable onUnlocked, onSwitch;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private LinearLayout card;
    private TextView notice;
    private EditText name, credential, confirmation;
    private JSONObject selected;
    private boolean emailMode, creating, busy;
    private int generation;

    public AccountGate(Activity activity, Runnable onUnlocked, Runnable onSwitch) {
        super(activity); this.activity = activity; this.onUnlocked = onUnlocked; this.onSwitch = onSwitch;
        profiles = new ProfileStore(activity);
        setFillViewport(true); setBackgroundColor(Color.rgb(7, 11, 15));
        LinearLayout outer = new LinearLayout(activity);
        outer.setGravity(Gravity.CENTER); outer.setOrientation(LinearLayout.VERTICAL);
        outer.setPadding(dp(24), dp(40), dp(24), dp(40));
        addView(outer, new ScrollView.LayoutParams(-1, -1));
        card = new LinearLayout(activity); card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(24), dp(24), dp(24), dp(24));
        GradientDrawable background = new GradientDrawable(); background.setColor(Color.rgb(18, 27, 36));
        background.setCornerRadius(dp(16)); background.setStroke(dp(1), Color.rgb(38, 53, 66)); card.setBackground(background);
        outer.addView(card, new LinearLayout.LayoutParams(Math.min(getResources().getDisplayMetrics().widthPixels - dp(48), dp(440)), -2));
        picker();
    }
    private boolean alive() { return !activity.isFinishing() && !activity.isDestroyed(); }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private TextView text(String value, int size) {
        TextView view = new TextView(activity); view.setText(value); view.setTextSize(size);
        view.setTextColor(Color.rgb(158, 178, 194)); view.setPadding(0, dp(8), 0, dp(8)); card.addView(view); return view;
    }
    private void header(String title) {
        card.removeAllViews(); name = credential = confirmation = null;
        TextView brand = text("YOUR PIT BOX", 12); brand.setLetterSpacing(.16f); brand.setTextColor(Color.rgb(73, 209, 125));
        TextView heading = text(title, 28); heading.setTypeface(null, Typeface.BOLD); heading.setTextColor(Color.rgb(238, 244, 248));
    }
    private Button button(String title, boolean primary, Runnable action) {
        Button b = new Button(activity); b.setText(title); b.setAllCaps(false); b.setMinHeight(dp(48));
        b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(primary ? Color.rgb(73, 209, 125) : Color.TRANSPARENT));
        b.setTextColor(primary ? Color.rgb(7, 16, 10) : Color.rgb(238, 244, 248));
        card.addView(b, new LinearLayout.LayoutParams(-1, -2));
        b.setOnClickListener(v -> { if (!busy) action.run(); }); return b;
    }
    private EditText field(String hint, int type, int max) {
        EditText input = new EditText(activity); input.setHint(hint); input.setContentDescription(hint);
        input.setInputType(type); input.setSingleLine(true); input.setTextColor(Color.WHITE);
        input.setHintTextColor(Color.rgb(158, 178, 194)); input.setTextSize(17); input.setMinHeight(dp(52));
        input.setFilters(new InputFilter[]{new InputFilter.LengthFilter(max)});
        if (android.os.Build.VERSION.SDK_INT >= 26) input.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        input.setSaveEnabled(false);
        card.addView(input, new LinearLayout.LayoutParams(-1, -2)); return input;
    }
    private void picker() {
        generation++; selected = null; header("Choose your driver"); notice = text("Opening profiles…", 14); busy = true;
        int request = generation;
        new Thread(() -> {
            try {
                JSONArray items = profiles.list();
                handler.post(() -> {
                    if (!alive() || generation != request) return; busy = false;
                    if (items.length() == 0) { creating = true; emailMode = false; form(); return; }
                    header("Choose your driver"); text("Each profile has its own history, settings and API keys.", 14);
                    try {
                        for (int i = 0; i < items.length(); i++) {
                            JSONObject item = items.getJSONObject(i);
                            button(item.getString("name"), false, () -> {
                                String active = AccountSession.profileId();
                                if (active != null && !active.equals(item.optString("id"))) { onSwitch.run(); return; }
                                selected = item; creating = false; emailMode = "email".equals(item.optString("method")); form();
                            });
                        }
                    } catch (Exception invalid) { text("Profiles could not be read. Your data has not been changed.", 14); return; }
                    button("+ New driver", true, () -> {
                        if (AccountSession.profileId() != null) { onSwitch.run(); return; }
                        creating = true; emailMode = false; selected = null; form();
                    });
                });
            } catch (Exception error) { handler.post(() -> { if (alive() && generation == request) { busy = false; notice.setText("The encrypted profile store is unavailable. Your data has not been changed."); } }); }
        }, "pitbox-profiles").start();
    }
    private void form() {
        generation++; header(creating ? "Set up your driver" : "Welcome back, " + selected.optString("name"));
        text("Your API keys stay encrypted on this device.", 14);
        TextView methodLabel = text("Email OR PIN", 18);
        methodLabel.setTypeface(null, Typeface.BOLD);
        methodLabel.setTextColor(Color.rgb(238, 244, 248));
        if (creating) {
            name = field("Driver name", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_WORDS, 40);
            text("Choose either email or a 4-digit PIN.", 14);
            button(emailMode ? "Use a PIN instead" : "Use email instead", false, () -> { emailMode = !emailMode; form(); });
        }
        credential = field(emailMode ? "Email address" : "4-digit PIN", emailMode
            ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS
            : InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD, emailMode ? 254 : 4);
        if (creating && !emailMode) confirmation = field("Confirm PIN", InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD, 4);
        text(emailMode ? "No verification email. Anyone who knows this address can open this profile. Choose PIN for an app lock."
            : "Your PIN stays on this device. Keep it safe: PIN-only profiles have no email recovery.", 12);
        text("Your email and PIN are not sent out. Sign-in records use a random profile ID, time and app version. No API keys or driving data are included.", 12);
        if (creating) text("The first driver keeps this device's existing history and settings. New drivers start with separate data.", 12);
        notice = text("", 13);
        button(creating ? "Create profile and open" : "Open my pit box", true, this::unlock);
        button("Choose another driver", false, this::picker);
    }
    private void unlock() {
        if (busy) return;
        final String value = credential.getText().toString().trim();
        final String address = emailMode ? value.toLowerCase(Locale.ROOT) : "";
        final String label = creating ? name.getText().toString().trim() : selected.optString("name");
        if (emailMode && !android.util.Patterns.EMAIL_ADDRESS.matcher(address).matches()) { notice.setText("Enter a valid email address."); return; }
        if (!emailMode && !value.matches("[0-9]{4}")) { notice.setText("Enter exactly four digits."); return; }
        if (creating && !emailMode && !value.equals(confirmation.getText().toString())) { notice.setText("The PINs do not match."); return; }
        if (creating && label.isEmpty()) { notice.setText("Enter a driver name."); return; }
        final int request = generation;
        final boolean newProfile = creating, email = emailMode;
        final String selectedId = selected == null ? null : selected.optString("id");
        busy = true; notice.setText("Opening your profile…");
        new Thread(() -> {
            try {
                JSONObject profile;
                if (newProfile) profile = profiles.create(label, email ? "email" : "pin", address,
                    email ? "" : value, null, AccountSession.randomSecret());
                else {
                    profile = profiles.find(selectedId);
                    boolean accepted = email ? java.security.MessageDigest.isEqual(address.getBytes(java.nio.charset.StandardCharsets.UTF_8),
                        profile.getString("email").getBytes(java.nio.charset.StandardCharsets.UTF_8)) : profiles.checkPin(selectedId, value);
                    if (!accepted) throw new IllegalArgumentException(email ? "That email does not match this profile." : "Incorrect PIN.");
                }
                // A successful local authentication is durably queued before opening.
                ProfileEvents.enqueue(activity, profile);
                JSONObject unlocked = profile;
                handler.post(() -> {
                    if (!alive() || generation != request || getWindowVisibility() != VISIBLE) return;
                    busy = false; credential.setText(""); if (confirmation != null) confirmation.setText("");
                    AccountSession.unlockProfile(unlocked.optString("id"), unlocked.optBoolean("legacy"));
                    ((android.view.inputmethod.InputMethodManager) activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE))
                        .hideSoftInputFromWindow(getWindowToken(), 0);
                    ProfileEvents.start(activity); onUnlocked.run();
                });
            } catch (Exception error) {
                final String message = error instanceof IllegalArgumentException ? error.getMessage() : "Could not open the encrypted profile. Please retry.";
                handler.post(() -> { if (!alive() || generation != request) return; busy = false;
                    notice.setText(message); if (credential != null) credential.setText(""); });
            }
        }, "pitbox-unlock").start();
    }
    @Override protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
        super.onSizeChanged(width, height, oldWidth, oldHeight);
        if (card != null && width > 0) {
            LinearLayout.LayoutParams p = (LinearLayout.LayoutParams) card.getLayoutParams();
            p.width = Math.min(Math.max(1, width - dp(48)), dp(440)); card.setLayoutParams(p);
        }
    }
    @Override protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(visibility);
        if (visibility == VISIBLE && card != null) { picker(); }
        else if (card != null) {
            generation++; busy = false;
            if (credential != null) credential.setText(""); if (confirmation != null) confirmation.setText("");
        }
    }
    @Override protected void onDetachedFromWindow() { generation++; handler.removeCallbacksAndMessages(null); super.onDetachedFromWindow(); }
}
