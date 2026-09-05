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

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(ContextCompat.getColor(this, R.color.pitbox_bg));

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
        requestNotificationPermission();
        startBackend();
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
    }

    private void startBackend() {
        Intent service = new Intent(this, PitBoxService.class);
        ContextCompat.startForegroundService(this, service);
        handler.post(this::pollUntilReady);
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
            new Thread(() -> {
                boolean ready = healthy(url + "api/health");
                handler.post(() -> {
                    if (ready && !loaded) {
                        loaded = true;
                        status.setVisibility(View.GONE);
                        web.setVisibility(View.VISIBLE);
                        web.loadUrl(url);
                    } else {
                        handler.postDelayed(this::pollUntilReady, 400);
                    }
                });
            }, "pitbox-health").start();
            return;
        }
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
