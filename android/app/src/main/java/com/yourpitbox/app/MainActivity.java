package com.yourpitbox.app;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebSettings;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

import androidx.core.content.ContextCompat;

import java.net.HttpURLConnection;
import java.net.URL;

import org.json.JSONObject;

/**
 * The dashboard, in a WebView, over the backend the service is running.
 *
 * The page is the same static/index.html the desktop opens in a browser. This
 * activity only supplies what a browser tab would: a viewport, JavaScript,
 * storage for the dashboard's remembered layout, and a screen that stays on
 * during a session.
 */
public class MainActivity extends Activity {
    private WebView web;
    private TextView status;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean loaded = false;
    private long startedAt = 0;
    private boolean pageReady;
    private String pendingInvitation;
    private int invitationAttempts;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(ContextCompat.getColor(this, R.color.pitbox_bg));
        // Android 15 draws the window edge to edge, under the status bar and
        // the gesture bar. The dashboard has its own header and footer at the
        // window's edges, so the layout is inset by the bars (and the keyboard
        // when it is up), leaving the bars over the page's own dark ground.
        androidx.core.view.ViewCompat.setOnApplyWindowInsetsListener(root, (view, insets) -> {
            androidx.core.graphics.Insets bars = insets.getInsets(
                    androidx.core.view.WindowInsetsCompat.Type.systemBars()
                            | androidx.core.view.WindowInsetsCompat.Type.displayCutout()
                            | androidx.core.view.WindowInsetsCompat.Type.ime());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return androidx.core.view.WindowInsetsCompat.CONSUMED;
        });

        web = new WebView(this);
        web.setBackgroundColor(ContextCompat.getColor(this, R.color.pitbox_bg));
        web.setVisibility(View.INVISIBLE);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri target = request.getUrl();
                if (isTrustedDashboard(target.toString())) return false;
                // An external link opens in a browser; pairing invitations are
                // never injected into a page outside the embedded dashboard.
                String scheme = target.getScheme();
                if (request.isForMainFrame() && request.hasGesture()
                        && ("https".equals(scheme) || "http".equals(scheme))) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, target)); }
                    catch (android.content.ActivityNotFoundException ignored) { /* no browser installed */ }
                }
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                pageReady = false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                pageReady = isTrustedDashboard(url);
                deliverPairingInvitation();
            }
        });
        root.addView(web, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        status = new TextView(this);
        status.setText(R.string.starting);
        status.setTextColor(Color.parseColor("#9eb2c2"));
        status.setTextSize(15);
        status.setGravity(Gravity.CENTER_HORIZONTAL);
        status.setPadding(48, 96, 48, 96);
        // Selectable and scrollable: a traceback must be readable and
        // copyable on the device, with no computer attached.
        status.setTextIsSelectable(true);
        android.widget.ScrollView scroller = new android.widget.ScrollView(this);
        scroller.setFillViewport(true);
        scroller.addView(status, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT));
        root.addView(scroller, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        setContentView(root);
        capturePairingInvitation(getIntent());
        if (!requestPermissionsFirst()) startBackend();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        capturePairingInvitation(intent);
        deliverPairingInvitation();
    }

    /** Custom-scheme input is untrusted; it only pre-fills the pairing form. */
    private void capturePairingInvitation(Intent intent) {
        if (intent == null || !Intent.ACTION_VIEW.equals(intent.getAction())) return;
        Uri data = intent.getData();
        if (data == null || data.toString().length() > 8192 || !data.isHierarchical()
                || !"pitwall".equals(data.getScheme()) || !"pair".equals(data.getHost())
                || data.getPort() != -1 || data.getUserInfo() != null || data.getFragment() != null) return;
        String path = data.getPath();
        if (path != null && !path.isEmpty() && !"/".equals(path)) return;
        if (data.getQueryParameterNames().size() != 1 || data.getQueryParameters("invite").size() != 1) return;
        String invitation = data.getQueryParameter("invite");
        if (invitation == null || invitation.isEmpty() || invitation.length() > 4096) return;
        pendingInvitation = invitation;
        invitationAttempts = 0;
    }

    private boolean isTrustedDashboard(String url) {
        String base = PitBoxService.getDashboardUrl();
        if (base == null || url == null) return false;
        Uri expected = Uri.parse(base);
        Uri actual = Uri.parse(url);
        return "http".equals(actual.getScheme()) && "127.0.0.1".equals(actual.getHost())
                && actual.getHost().equals(expected.getHost()) && actual.getPort() == expected.getPort()
                && actual.getUserInfo() == null;
    }

    private void deliverPairingInvitation() {
        if (pendingInvitation == null || !pageReady || isDestroyed() || isFinishing()
                || web == null || !isTrustedDashboard(web.getUrl())) return;
        String invitation = pendingInvitation;
        String script = "(function(){if(!window.PitWallTransfers || "
                + "typeof window.PitWallTransfers.acceptInvitation !== 'function') return 'waiting';"
                + "window.PitWallTransfers.acceptInvitation(" + JSONObject.quote(invitation) + ");"
                + "return 'accepted';})()";
        web.evaluateJavascript(script, result -> {
            if (!invitation.equals(pendingInvitation)) return;
            if ("\"accepted\"".equals(result)) pendingInvitation = null;
            else if (++invitationAttempts < 20) handler.postDelayed(this::deliverPairingInvitation, 250);
        });
    }

    /**
     * The microphone decides whether the backend starts its voice layer, and
     * that is read once at start, so the question is asked before the
     * service is started. Returns true when a prompt is showing; the backend
     * then starts from the permission callback, whatever the answer.
     */
    private boolean requestPermissionsFirst() {
        java.util.List<String> wanted = new java.util.ArrayList<>();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            wanted.add(Manifest.permission.RECORD_AUDIO);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            wanted.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (wanted.isEmpty()) return false;
        requestPermissions(wanted.toArray(new String[0]), 1);
        return true;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == 1 && startedAt == 0) startBackend();
    }

    private void startBackend() {
        Intent service = new Intent(this, PitBoxService.class);
        ContextCompat.startForegroundService(this, service);
        startedAt = System.currentTimeMillis();
        handler.post(this::pollUntilReady);
    }

    /** The backend reports "http://127.0.0.1:8000" with no trailing slash. */
    private static String join(String base, String path) {
        return base.endsWith("/") ? base + path : base + "/" + path;
    }

    /**
     * What the waiting screen says. A first start on a phone can take a
     * while (the interpreter unpacks, the backend imports numpy), so the
     * stage and the elapsed time are shown; past 45 seconds the tail of the
     * backend's own log is shown too, so a stall can be read off the screen
     * without a computer attached.
     */
    private void describeWait(String stage) {
        long seconds = (System.currentTimeMillis() - startedAt) / 1000;
        StringBuilder text = new StringBuilder(getString(R.string.starting));
        text.append("\n\n").append(stage).append(" · ").append(seconds).append(" s");
        if (seconds >= 45) {
            text.append("\n\nThis is taking longer than it should. The backend's log so far:\n\n")
                .append(logTail());
        }
        status.setText(text.toString());
    }

    private String logTail() {
        java.io.File log = new java.io.File(getFilesDir(), "PitWallData/pitwall.log");
        if (!log.isFile()) return "(no pitwall.log yet: the backend has not started logging)";
        try (java.io.RandomAccessFile file = new java.io.RandomAccessFile(log, "r")) {
            long length = file.length();
            int want = (int) Math.min(length, 3000);
            byte[] bytes = new byte[want];
            file.seek(length - want);
            file.readFully(bytes);
            return new String(bytes, "UTF-8");
        } catch (java.io.IOException error) {
            return "(could not read pitwall.log: " + error + ")";
        }
    }

    /** Loads the dashboard the moment the backend answers its health check. */
    private void pollUntilReady() {
        if (loaded || isDestroyed() || isFinishing()) return;
        String failure = PitBoxService.getFailure();
        if (failure != null) {
            status.setText(getString(R.string.start_failed) + "\n\n" + failure
                    + "\n\nBackend log:\n\n" + logTail());
            return;
        }
        String url = PitBoxService.getDashboardUrl();
        if (url != null && PitBoxService.isRunning()) {
            describeWait("Backend running, waiting for the dashboard at " + url);
            String healthUrl = join(url, "api/health");
            new Thread(() -> {
                boolean ready = healthy(healthUrl);
                handler.post(() -> {
                    if (isDestroyed() || isFinishing()) return;
                    if (ready && !loaded) {
                        loaded = true;
                        status.setVisibility(View.GONE);
                        web.setVisibility(View.VISIBLE);
                        web.loadUrl(join(url, ""));
                    } else {
                        handler.postDelayed(this::pollUntilReady, 400);
                    }
                });
            }, "pitbox-health").start();
            return;
        }
        describeWait(url == null ? "Starting the backend service" : "Backend starting");
        handler.postDelayed(this::pollUntilReady, 400);
    }

    private static boolean healthy(String healthUrl) {
        try {
            HttpURLConnection connection = (HttpURLConnection) new URL(healthUrl).openConnection();
            connection.setConnectTimeout(500);
            connection.setReadTimeout(500);
            int code = connection.getResponseCode();
            connection.disconnect();
            return code == 200;
        } catch (Exception ignored) {
            return false;
        }
    }

    /**
     * Quit in the dashboard stops the backend, and the service then ends the
     * process, as Quit does on the desktop. If this activity is somehow still
     * here with nothing running and no failure to show, leave, so the next
     * tap on the icon starts clean rather than showing a dead page.
     */
    @Override
    protected void onResume() {
        super.onResume();
        if (loaded && !PitBoxService.isRunning() && PitBoxService.getFailure() == null) {
            finishAndRemoveTask();
        }
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.getVisibility() == View.VISIBLE && web.canGoBack()) {
            web.goBack();
            return;
        }
        // Leaving the activity does not stop the session: the service keeps
        // receiving telemetry. Stop comes from the notification or Quit.
        moveTaskToBack(true);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
