package com.yourpitbox.app;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

import androidx.core.content.ContextCompat;

import java.net.HttpURLConnection;
import java.net.URL;

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
        web.setWebViewClient(new WebViewClient());
        root.addView(web, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        status = new TextView(this);
        status.setText(R.string.starting);
        status.setTextColor(Color.parseColor("#9eb2c2"));
        status.setTextSize(16);
        status.setGravity(Gravity.CENTER);
        status.setPadding(48, 48, 48, 48);
        root.addView(status, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        setContentView(root);
        if (!requestPermissionsFirst()) startBackend();
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
        if (loaded) return;
        String failure = PitBoxService.getFailure();
        if (failure != null) {
            status.setText(getString(R.string.start_failed) + "\n\n" + failure);
            return;
        }
        String url = PitBoxService.getDashboardUrl();
        if (url != null && PitBoxService.isRunning()) {
            describeWait("Backend running, waiting for the dashboard at " + url);
            String healthUrl = join(url, "api/health");
            new Thread(() -> {
                boolean ready = healthy(healthUrl);
                handler.post(() -> {
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
     * Quit in the dashboard stops the service and leaves the "stopping" page
     * in the WebView. Coming back to the app after that should start a fresh
     * session, not show the stale page: the backend's own run() has returned
     * by the time the service reports not running, so the ports are free.
     */
    @Override
    protected void onResume() {
        super.onResume();
        if (loaded && !PitBoxService.isRunning() && PitBoxService.getFailure() == null) {
            loaded = false;
            web.setVisibility(View.INVISIBLE);
            web.loadUrl("about:blank");
            status.setText(R.string.starting);
            status.setVisibility(View.VISIBLE);
            startBackend();
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
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
