package com.yourpitbox.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.webkit.WebView;

import androidx.webkit.WebMessageCompat;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;

import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.util.Collections;

/** App-owned dashboard preferences survive a change of the backend's port. */
final class DashboardPreferencesBridge {
    private static final String KEY = "ypb-driver-dashboard-v2";
    private static final int MAX_BYTES = 64 * 1024;

    static void install(Context context, WebView web, String dashboardUrl) {
        Uri expected = Uri.parse(dashboardUrl);
        if (!"http".equals(expected.getScheme()) || !"127.0.0.1".equals(expected.getHost())
                || expected.getUserInfo() != null || expected.getPort() < 1
                || !WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) return;
        String origin = "http://127.0.0.1:" + expected.getPort();
        SharedPreferences prefs = context.getSharedPreferences("driver_dashboard", Context.MODE_PRIVATE);
        // The object is injected only into the backend's exact origin, including
        // its local dashboard iframe. External pages/iframes never receive it.
        WebViewCompat.addWebMessageListener(web, "PitBoxDashboardPreferences",
                Collections.singleton(origin), (view, message, sourceOrigin, isMainFrame, reply) -> {
                    if (!"http".equals(sourceOrigin.getScheme())
                            || !"127.0.0.1".equals(sourceOrigin.getHost())
                            || sourceOrigin.getPort() != expected.getPort()
                            || sourceOrigin.getUserInfo() != null
                            || message.getType() != WebMessageCompat.TYPE_STRING) return;
                    String data = message.getData();
                    if (data == null || data.length() > MAX_BYTES * 8) return;
                    try {
                        JSONObject request = new JSONObject(data);
                        String id = request.getString("id");
                        String op = request.getString("op");
                        if (id.isEmpty() || id.length() > 80) return;
                        JSONObject result = new JSONObject().put("id", id).put("op", op);
                        if ("load".equals(op)) {
                            String saved = prefs.getString(KEY, null);
                            result.put("ok", true).put("value", saved == null ? JSONObject.NULL : saved);
                        } else if ("save".equals(op)) {
                            Object value = request.opt("value");
                            boolean valid = value instanceof String && validPreferences((String) value);
                            // Commit before acknowledging: a reload or process
                            // restart after the reply must retain this layout.
                            boolean saved = valid && prefs.edit().putString(KEY, (String) value).commit();
                            result.put("ok", saved);
                        } else {
                            result.put("ok", false);
                        }
                        reply.postMessage(result.toString());
                    } catch (JSONException ignored) {
                        // Malformed messages have no authenticated request ID.
                    }
                });
    }

    private static boolean validPreferences(String value) {
        if (value.getBytes(StandardCharsets.UTF_8).length > MAX_BYTES) return false;
        try {
            new JSONObject(value);
            return true;
        } catch (JSONException ignored) {
            return false;
        }
    }
}
