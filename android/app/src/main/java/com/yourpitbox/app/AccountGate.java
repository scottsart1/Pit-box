package com.yourpitbox.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.text.InputFilter;
import android.text.InputType;
import android.util.Log;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.Locale;

/**
 * The native driver-profile screen shown once per app start.
 *
 * Each driver opens their profile with an email address (an identifier, not a
 * secret) or a 4-digit PIN (an app lock). The first profile created on a
 * device inherits everything stored before profiles existed. Nothing here is
 * sent anywhere; a successful opening is queued as a pseudonymous sign-in.
 */
public final class AccountGate extends ScrollView {
    private static final String TAG = "PitBoxProfiles";
    private enum Mode { LOADING, PICKER, SETUP, UNLOCK, FAILED }

    private final Activity activity;
    private final ProfileStore store;
    private final Runnable onUnlocked;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final LinearLayout card;
    private Mode mode = Mode.LOADING;
    private TextView notice;
    private EditText name, credential, confirmation;
    private Button submit;
    private JSONArray profiles = new JSONArray();
    private JSONObject selected;
    private boolean existingData, emailMode, busy;
    private String draftName = "";

    public AccountGate(Activity activity, Runnable onUnlocked) {
        super(activity);
        this.activity = activity; this.onUnlocked = onUnlocked;
        store = new ProfileStore(activity);
        setFillViewport(true); setBackgroundColor(color(R.color.pitbox_bg));
        LinearLayout outer = new LinearLayout(activity);
        outer.setGravity(Gravity.CENTER); outer.setOrientation(LinearLayout.VERTICAL);
        outer.setPadding(dp(16), dp(32), dp(16), dp(32));
        addView(outer, new ScrollView.LayoutParams(-1, -1));
        card = new LinearLayout(activity); card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(24), dp(24), dp(24), dp(24));
        card.setBackground(outline(R.color.pitbox_panel, R.color.pitbox_line, 16));
        outer.addView(card, new LinearLayout.LayoutParams(cardWidth(getResources().getDisplayMetrics().widthPixels), -2));
        load();
    }

    /** Called when the opening animation ends and this screen is uncovered. */
    public void reveal() {
        setVisibility(VISIBLE);
        if (mode == Mode.UNLOCK) focusCredential();
    }

    // ----- Screens ---------------------------------------------------------

    private void load() {
        mode = Mode.LOADING; busy = true;
        header(R.string.gate_choose_title);
        notice = text(getContext().getString(R.string.gate_loading), 15, R.color.pitbox_muted);
        new Thread(() -> {
            try {
                JSONArray items = store.list();
                boolean inherited = ProfilePaths.hasExistingData(activity.getFilesDir());
                handler.post(() -> {
                    if (!alive()) return;
                    busy = false; profiles = items; existingData = inherited;
                    if (items.length() == 0) setup();
                    else if (items.length() == 1) unlockForm(items.optJSONObject(0));
                    else picker();
                });
            } catch (Exception error) {
                Log.w(TAG, "Encrypted profile index unavailable: " + error.getClass().getSimpleName());
                handler.post(() -> { if (alive()) { busy = false; failed(); } });
            }
        }, "pitbox-profiles").start();
    }

    private void picker() {
        mode = Mode.PICKER; selected = null;
        header(R.string.gate_choose_title);
        text(getContext().getString(R.string.gate_choose_body), 15, R.color.pitbox_muted);
        for (int i = 0; i < profiles.length(); i++) {
            JSONObject item = profiles.optJSONObject(i);
            if (item != null) driverRow(item);
        }
        spacer(8);
        button(R.string.gate_new_driver, true, R.id.gate_new_driver, () -> { draftName = ""; emailMode = false; setup(); });
    }

    /** First run, upgrade from a build without profiles, or an added driver. */
    private void setup() {
        mode = Mode.SETUP; selected = null;
        boolean first = profiles.length() == 0;
        header(first ? R.string.gate_setup_title : R.string.gate_add_title);
        if (first && existingData) {
            text(getContext().getString(R.string.gate_upgrade_body), 15, R.color.pitbox_muted);
            callout(getContext().getString(R.string.gate_upgrade_callout));
        } else {
            text(getContext().getString(first ? R.string.gate_first_body : R.string.gate_new_body), 15, R.color.pitbox_muted);
        }
        name = field(R.string.gate_name_hint, InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_WORDS,
            ProfileStore.MAX_NAME, R.id.gate_name);
        name.setText(draftName);
        TextView method = text(getContext().getString(R.string.gate_method_label), 17, R.color.pitbox_text);
        method.setTypeface(null, Typeface.BOLD);
        heading(method);
        text(getContext().getString(R.string.gate_method_help), 14, R.color.pitbox_muted);
        methodChoice();
        credentialFields(true);
        chain(name, credential, confirmation);
        spacer(4);
        submit = button(R.string.gate_create, true, R.id.gate_submit, this::submit);
        if (!first) button(R.string.gate_other_driver, false, R.id.gate_other_driver, this::picker);
    }

    private void unlockForm(JSONObject profile) {
        mode = Mode.UNLOCK; selected = profile;
        emailMode = "email".equals(profile.optString("method"));
        header(0);
        TextView title = text(getContext().getString(R.string.gate_welcome, profile.optString("name")), 26, R.color.pitbox_text);
        title.setTypeface(null, Typeface.BOLD); heading(title);
        text(getContext().getString(emailMode ? R.string.gate_unlock_email : R.string.gate_unlock_pin), 15, R.color.pitbox_muted);
        credentialFields(false);
        chain(credential);
        submit = button(R.string.gate_open, true, R.id.gate_submit, this::submit);
        button(R.string.gate_other_driver, false, R.id.gate_other_driver, this::picker);
        if (getVisibility() == VISIBLE) focusCredential();
    }

    private void failed() {
        mode = Mode.FAILED;
        header(R.string.gate_vault_title);
        text(getContext().getString(R.string.gate_vault_body), 15, R.color.pitbox_muted);
        notice = text("", 14, R.color.pitbox_red);
        button(R.string.gate_retry, true, R.id.gate_retry, this::load);
        button(R.string.gate_reset, false, R.id.gate_reset, () -> new AlertDialog.Builder(activity)
            .setTitle(R.string.gate_reset_title).setMessage(R.string.gate_reset_body)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.gate_reset_confirm, (dialog, which) -> reset()).show());
    }

    private void reset() {
        busy = true;
        new Thread(() -> {
            boolean done;
            try { KeyVault.initialize(activity).reset(); done = true; }
            catch (Exception error) { Log.w(TAG, "Vault reset failed: " + error.getClass().getSimpleName()); done = false; }
            boolean succeeded = done;
            handler.post(() -> {
                if (!alive()) return;
                busy = false;
                if (succeeded) load(); else notice.setText(R.string.gate_reset_failed);
            });
        }, "pitbox-vault-reset").start();
    }

    // ----- Opening a profile ----------------------------------------------

    private void submit() {
        if (busy || credential == null) return;
        String value = credential.getText().toString().trim();
        String address = emailMode ? value.toLowerCase(Locale.ROOT) : "";
        boolean creating = mode == Mode.SETUP;
        String label = creating ? name.getText().toString().trim() : selected.optString("name");
        if (creating && label.isEmpty()) { error(R.string.gate_error_name); name.requestFocus(); return; }
        if (emailMode && !android.util.Patterns.EMAIL_ADDRESS.matcher(address).matches()) { error(R.string.gate_error_email); return; }
        if (!emailMode && !value.matches("[0-9]{4}")) { error(R.string.gate_error_digits); return; }
        if (creating && !emailMode && !value.equals(confirmation.getText().toString())) {
            error(R.string.gate_error_mismatch); confirmation.setText(""); confirmation.requestFocus(); return;
        }
        boolean email = emailMode;
        String selectedId = selected == null ? null : selected.optString("id");
        busy = true; submit.setEnabled(false); progress(R.string.gate_opening);
        new Thread(() -> {
            try {
                JSONObject profile;
                if (creating) profile = store.create(label, email ? "email" : "pin", address,
                    email ? "" : value, null, AccountSession.randomSecret());
                else {
                    boolean accepted = email ? store.checkEmail(selectedId, address) : store.checkPin(selectedId, value);
                    if (!accepted) throw new ProfileStore.Rejected(email
                        ? ProfileStore.Reason.WRONG_EMAIL : ProfileStore.Reason.WRONG_PIN);
                    profile = store.find(selectedId);
                }
                // Every successful opening is queued before the profile opens.
                try { ProfileEvents.enqueue(activity, profile); }
                catch (Exception queue) { Log.w(TAG, "Sign-in could not be queued: " + queue.getClass().getSimpleName()); }
                // Process-wide: a recreated activity finds this profile open.
                AccountSession.unlockProfile(profile.getString("id"), profile.optString("name"), profile.optBoolean("legacy"));
                ProfileEvents.start(activity);
                handler.post(() -> {
                    if (!alive()) return;
                    busy = false; clearSecrets();
                    InputMethodManager keyboard = (InputMethodManager) activity.getSystemService(Context.INPUT_METHOD_SERVICE);
                    if (keyboard != null) keyboard.hideSoftInputFromWindow(getWindowToken(), 0);
                    onUnlocked.run();
                });
            } catch (Exception failure) {
                int message = message(failure);
                if (message == R.string.gate_error_open) Log.w(TAG, "Profile could not be opened: " + failure.getClass().getSimpleName());
                handler.post(() -> {
                    if (!alive()) return;
                    busy = false; submit.setEnabled(true); error(message);
                    if (credential != null) { credential.setText(""); credential.requestFocus(); }
                    if (confirmation != null) confirmation.setText("");
                });
            }
        }, "pitbox-unlock").start();
    }

    static int message(Exception failure) {
        if (!(failure instanceof ProfileStore.Rejected)) return R.string.gate_error_open;
        switch (((ProfileStore.Rejected) failure).reason) {
            case NAME: return R.string.gate_error_name;
            case NAME_TAKEN: return R.string.gate_error_name_taken;
            case EMAIL_TAKEN: return R.string.gate_error_email_taken;
            case LIMIT: return R.string.gate_error_limit;
            case WRONG_EMAIL: return R.string.gate_error_wrong_email;
            case WRONG_PIN: return R.string.gate_error_wrong_pin;
            case LOCKED: return R.string.gate_error_locked;
            case LOCKED_DAY: return R.string.gate_error_locked_day;
            default: return R.string.gate_error_open;
        }
    }

    // ----- Building blocks -------------------------------------------------

    private void methodChoice() {
        LinearLayout row = new LinearLayout(activity);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, dp(8), 0, dp(8));
        card.addView(row, new LinearLayout.LayoutParams(-1, -2));
        row.addView(choice(R.string.gate_method_pin, R.id.gate_method_pin, !emailMode, false), weighted(dp(4)));
        row.addView(choice(R.string.gate_method_email, R.id.gate_method_email, emailMode, true), weighted(0));
    }

    private Button choice(int label, int id, boolean active, boolean email) {
        Button b = new Button(activity);
        b.setId(id); b.setText(label); b.setAllCaps(false); b.setMinHeight(dp(48)); b.setTextSize(15);
        b.setBackground(outline(active ? R.color.pitbox_panel : R.color.pitbox_bg,
            active ? R.color.pitbox_green : R.color.pitbox_line, 8));
        b.setBackgroundTintList(null);
        b.setTextColor(color(active ? R.color.pitbox_green : R.color.pitbox_muted));
        b.setSelected(active);
        b.setOnClickListener(v -> {
            if (busy || emailMode == email) return;
            draftName = name == null ? "" : name.getText().toString();
            emailMode = email; setup();
            if (credential != null && !draftName.trim().isEmpty()) credential.requestFocus();
        });
        return b;
    }

    private LinearLayout.LayoutParams weighted(int rightMargin) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, -2, 1);
        p.rightMargin = rightMargin; p.leftMargin = rightMargin == 0 ? dp(4) : 0;
        return p;
    }

    private void credentialFields(boolean creating) {
        boolean confirm = creating && !emailMode;
        credential = field(emailMode ? R.string.gate_email_hint : R.string.gate_pin_hint,
            emailMode ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS
                : InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD,
            emailMode ? 254 : 4, R.id.gate_credential);
        confirmation = confirm ? field(R.string.gate_confirm_hint,
            InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD, 4, R.id.gate_confirm) : null;
        if (creating) text(getContext().getString(emailMode ? R.string.gate_email_note : R.string.gate_pin_note), 13, R.color.pitbox_muted);
        text(getContext().getString(R.string.gate_local_note), 13, R.color.pitbox_muted);
        notice = text("", 14, R.color.pitbox_red);
        notice.setId(R.id.gate_notice);
        notice.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
    }

    private void driverRow(JSONObject profile) {
        String label = profile.optString("name");
        boolean email = "email".equals(profile.optString("method"));
        LinearLayout row = new LinearLayout(activity);
        row.setOrientation(LinearLayout.HORIZONTAL); row.setGravity(Gravity.CENTER_VERTICAL);
        row.setMinimumHeight(dp(64)); row.setPadding(dp(16), dp(12), dp(16), dp(12));
        row.setBackground(outline(R.color.pitbox_bg, R.color.pitbox_line, 10));
        row.setClickable(true); row.setFocusable(true);
        String detail = getContext().getString(email ? R.string.gate_opens_with_email : R.string.gate_opens_with_pin);
        row.setContentDescription(label + ". " + detail);
        TextView avatar = new TextView(activity);
        avatar.setText(label.isEmpty() ? "?" : label.substring(0, label.offsetByCodePoints(0, 1)).toUpperCase(Locale.getDefault()));
        avatar.setGravity(Gravity.CENTER); avatar.setTextColor(color(R.color.pitbox_green)); avatar.setTypeface(null, Typeface.BOLD);
        GradientDrawable circle = new GradientDrawable(); circle.setShape(GradientDrawable.OVAL);
        circle.setStroke(dp(1), color(R.color.pitbox_line)); avatar.setBackground(circle);
        avatar.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        row.addView(avatar, new LinearLayout.LayoutParams(dp(40), dp(40)));
        LinearLayout labels = new LinearLayout(activity); labels.setOrientation(LinearLayout.VERTICAL);
        labels.setPadding(dp(16), 0, 0, 0);
        TextView title = new TextView(activity); title.setText(label); title.setTextSize(17);
        title.setTextColor(color(R.color.pitbox_text)); title.setTypeface(null, Typeface.BOLD);
        TextView subtitle = new TextView(activity); subtitle.setText(detail); subtitle.setTextSize(13);
        subtitle.setTextColor(color(R.color.pitbox_muted));
        labels.addView(title); labels.addView(subtitle);
        row.addView(labels, new LinearLayout.LayoutParams(0, -2, 1));
        row.setOnClickListener(v -> { if (!busy) unlockForm(profile); });
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
        p.topMargin = dp(10);
        card.addView(row, p);
    }

    private void header(int title) {
        card.removeAllViews(); name = credential = confirmation = null; submit = null; notice = null;
        TextView brand = text("YOUR PIT BOX", 12, R.color.pitbox_green);
        brand.setLetterSpacing(.16f); brand.setTypeface(null, Typeface.BOLD);
        brand.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        if (title != 0) {
            TextView heading = text(getContext().getString(title), 26, R.color.pitbox_text);
            heading.setTypeface(null, Typeface.BOLD); heading(heading);
        }
        scrollTo(0, 0);
    }

    private void heading(TextView view) {
        if (Build.VERSION.SDK_INT >= 28) view.setAccessibilityHeading(true);
    }

    private TextView text(String value, int size, int colorRes) {
        TextView view = new TextView(activity); view.setText(value); view.setTextSize(size);
        view.setTextColor(color(colorRes)); view.setPadding(0, dp(6), 0, dp(6));
        view.setLineSpacing(0, 1.15f);
        card.addView(view, new LinearLayout.LayoutParams(-1, -2)); return view;
    }

    private void callout(String value) {
        TextView view = text(value, 15, R.color.pitbox_text);
        view.setPadding(dp(14), dp(12), dp(14), dp(12));
        view.setBackground(outline(R.color.pitbox_bg, R.color.pitbox_green, 10));
        LinearLayout.LayoutParams p = (LinearLayout.LayoutParams) view.getLayoutParams();
        p.topMargin = dp(8); p.bottomMargin = dp(8); view.setLayoutParams(p);
    }

    private void spacer(int height) {
        card.addView(new View(activity), new LinearLayout.LayoutParams(1, dp(height)));
    }

    private Button button(int label, boolean primary, int id, Runnable action) {
        Button b = new Button(activity); b.setId(id); b.setText(label); b.setAllCaps(false);
        b.setMinHeight(dp(48)); b.setTextSize(16);
        b.setBackgroundTintList(ColorStateList.valueOf(primary ? color(R.color.pitbox_green) : 0));
        b.setTextColor(color(primary ? R.color.pitbox_bg : R.color.pitbox_text));
        if (primary) b.setTypeface(null, Typeface.BOLD);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
        p.topMargin = dp(6);
        card.addView(b, p);
        b.setOnClickListener(v -> { if (!busy) action.run(); }); return b;
    }

    private EditText field(int hint, int type, int max, int id) {
        EditText input = new EditText(activity); input.setId(id); input.setHint(hint);
        input.setInputType(type); input.setSingleLine(true); input.setTextColor(color(R.color.pitbox_text));
        input.setHintTextColor(color(R.color.pitbox_muted)); input.setTextSize(17); input.setMinHeight(dp(52));
        input.setFilters(new InputFilter[]{new InputFilter.LengthFilter(max)});
        input.setBackgroundTintList(ColorStateList.valueOf(color(R.color.pitbox_muted)));
        if (Build.VERSION.SDK_INT >= 26) input.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        // Never restore a typed PIN or address from saved instance state.
        input.setSaveEnabled(false);
        card.addView(input, new LinearLayout.LayoutParams(-1, -2)); return input;
    }

    /**
     * Next (or Enter on a hardware keyboard) moves through these fields in
     * order and Done on the last opens the profile. Android's own focus
     * search would stop on the method buttons between the name and the PIN
     * once a hardware key has taken the screen out of touch mode.
     */
    private void chain(EditText... fields) {
        for (int i = 0; i < fields.length; i++) {
            if (fields[i] == null) continue;
            EditText following = null;
            for (int j = i + 1; j < fields.length && following == null; j++) following = fields[j];
            EditText next = following;
            fields[i].setImeOptions((next == null ? EditorInfo.IME_ACTION_DONE : EditorInfo.IME_ACTION_NEXT)
                | EditorInfo.IME_FLAG_NO_EXTRACT_UI);
            fields[i].setOnEditorActionListener((view, action, event) -> {
                boolean enter = event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER;
                if (action == EditorInfo.IME_NULL && !enter) return false;
                if (event != null && event.getAction() != KeyEvent.ACTION_DOWN) return true;
                if (next != null) next.requestFocus(); else submit();
                return true;
            });
        }
    }

    private void error(int message) {
        if (notice == null) return;
        notice.setTextColor(color(R.color.pitbox_red)); notice.setText(message);
    }

    private void progress(int message) {
        if (notice == null) return;
        notice.setTextColor(color(R.color.pitbox_muted)); notice.setText(message);
    }

    private void focusCredential() {
        if (credential == null) return;
        credential.requestFocus();
        credential.post(() -> {
            InputMethodManager keyboard = (InputMethodManager) activity.getSystemService(Context.INPUT_METHOD_SERVICE);
            if (keyboard != null && credential != null && credential.hasFocus()) keyboard.showSoftInput(credential, InputMethodManager.SHOW_IMPLICIT);
        });
    }

    private void clearSecrets() {
        if (credential != null) credential.setText("");
        if (confirmation != null) confirmation.setText("");
    }

    private GradientDrawable outline(int fill, int stroke, int radius) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(color(fill)); shape.setCornerRadius(dp(radius)); shape.setStroke(dp(1), color(stroke));
        return shape;
    }

    private int color(int res) { return getContext().getColor(res); }
    private boolean alive() { return !activity.isFinishing() && !activity.isDestroyed(); }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private int cardWidth(int width) { return Math.min(Math.max(1, width - dp(32)), dp(440)); }

    @Override protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
        super.onSizeChanged(width, height, oldWidth, oldHeight);
        if (width > 0) {
            LinearLayout.LayoutParams p = (LinearLayout.LayoutParams) card.getLayoutParams();
            p.width = cardWidth(width); card.setLayoutParams(p);
        }
    }

    @Override protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(visibility);
        // Leaving the screen discards a half-typed PIN, never a finished opening.
        if (visibility != VISIBLE && !busy) clearSecrets();
    }

    @Override protected void onDetachedFromWindow() { handler.removeCallbacksAndMessages(null); super.onDetachedFromWindow(); }
}
