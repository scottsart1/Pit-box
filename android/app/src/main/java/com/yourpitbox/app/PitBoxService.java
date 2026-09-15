package com.yourpitbox.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.content.res.AssetManager;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import androidx.core.app.NotificationCompat;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Runs the Python backend for as long as a session lasts.
 *
 * On a phone the receiver has to outlive the screen and the activity: Android
 * kills background work freely, so the backend lives in a foreground service
 * with a persistent notification, holds a Wi-Fi lock so telemetry keeps
 * arriving with the screen off, and a partial wake lock so the engineer can
 * keep reasoning. Stopping the service is how the user quits the app.
 */
public class PitBoxService extends Service {
    private static final String TAG = "PitBoxService";
    private static final String CHANNEL_ID = "pitbox.session";
    private static final int NOTIFICATION_ID = 1;
    public static final String ACTION_STOP = "com.yourpitbox.app.STOP";

    private static volatile boolean running = false;
    private static volatile String dashboardUrl = null;
    private static volatile String failure = null;
    private static volatile PitBoxService instance;

    private Thread backend;
    private AndroidNetworkMonitor networkMonitor;
    private final List<String> lockWarnings = new ArrayList<>();
    private WifiManager.WifiLock wifiLock;
    private WifiManager.MulticastLock multicastLock;
    private PowerManager.WakeLock wakeLock;

    public static boolean isRunning() { return running; }
    public static String getDashboardUrl() { return dashboardUrl; }
    public static String getFailure() { return failure; }

    /** Read by Python through Chaquopy; the dashboard remains loopback-only. */
    public static String getNetworkStatusJson() {
        PitBoxService service = instance;
        return service == null ? "{\"platform\":\"android\",\"available\":false}" : service.networkStatusJson();
    }

    /** The duplicated descriptor is closed in the monitor, never the caller's. */
    public static boolean bindLocalSocket(int descriptor, String peerAddress) throws IOException {
        PitBoxService service = instance;
        return service != null && service.networkMonitor != null
                && service.networkMonitor.bindLocalSocket(descriptor, peerAddress);
    }

    private synchronized String networkStatusJson() {
        try {
            JSONObject status = networkMonitor == null ? new JSONObject() : new JSONObject(networkMonitor.snapshot());
            status.put("available", true);
            status.put("service_running", running);
            status.put("wifi_lock_held", wifiLock != null && wifiLock.isHeld());
            status.put("multicast_lock_held", multicastLock != null && multicastLock.isHeld());
            status.put("wake_lock_held", wakeLock != null && wakeLock.isHeld());
            JSONArray warnings = status.optJSONArray("warnings");
            if (warnings == null) warnings = new JSONArray();
            for (String warning : lockWarnings) warnings.put(warning);
            status.put("warnings", warnings);
            return status.toString();
        } catch (JSONException error) {
            return "{\"platform\":\"android\",\"available\":false}";
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        networkMonitor = new AndroidNetworkMonitor(this);
        networkMonitor.start();
        createChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopBackend();
            stopSelf();
            return START_NOT_STICKY;
        }
        try {
            startInForeground();
        } catch (RuntimeException error) {
            // Android can reject a background restart or a revoked microphone
            // grant. Keep the reason readable instead of crashing the activity.
            failure = describe(error);
            recordFailure(failure);
            stopBackend();
            stopSelf();
            return START_NOT_STICKY;
        }
        if (backend == null || !backend.isAlive()) {
            acquireLocks();
            failure = null;
            dashboardUrl = null;
            backend = new Thread(this::runBackend, "pitbox-backend");
            backend.start();
        }
        return START_STICKY;
    }

    private void runBackend() {
        try {
            File files = getFilesDir();
            File staticDir = installDashboard(files);
            PyObject entry = Python.getInstance().getModule("pitbox_android");
            entry.callAttr("configure", files.getAbsolutePath(), staticDir.getAbsolutePath(), microphoneGranted());
            dashboardUrl = entry.callAttr("dashboard_url").toString();
            running = true;
            // Blocks until the server is asked to stop (Quit in the dashboard,
            // or the notification's Stop action).
            entry.callAttr("start");
        } catch (Throwable error) {
            Log.e(TAG, "Backend stopped with an error", error);
            failure = describe(error);
            recordFailure(failure);
        } finally {
            running = false;
            releaseLocks();
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            if (failure == null) {
                // The backend keeps state a second start in the same process
                // cannot survive, exactly as on the desktop, where Quit ends
                // the process. Ending it here means the next tap on the icon
                // starts clean. On a failure the process stays so the
                // activity can show what went wrong.
                android.os.Process.killProcess(android.os.Process.myPid());
            }
        }
    }

    /** The Python traceback, not just the exception's one-line summary. */
    private static String describe(Throwable error) {
        String trace = Log.getStackTraceString(error);
        // Keep the end, where the Python frames and the message are.
        return trace.length() > 6000 ? "…" + trace.substring(trace.length() - 6000) : trace;
    }

    private void recordFailure(String text) {
        try {
            File data = new File(getFilesDir(), "PitWallData");
            //noinspection ResultOfMethodCallIgnored
            data.mkdirs();
            try (FileOutputStream out = new FileOutputStream(new File(data, "android-start-error.txt"))) {
                out.write(text.getBytes("UTF-8"));
            }
        } catch (IOException ignored) {
            // The screen still shows it.
        }
    }

    private void stopBackend() {
        try {
            Python.getInstance().getModule("pitbox_android").callAttr("stop");
        } catch (Throwable error) {
            Log.w(TAG, "Stop request failed", error);
        }
    }

    /**
     * Copies the dashboard out of the APK's assets so the backend can serve it
     * from a real directory. Re-copied whenever the app version changes, so an
     * update never serves last release's JavaScript against this release's
     * backend.
     */
    private File installDashboard(File files) throws IOException {
        File target = new File(files, "static");
        File stamp = new File(files, "static.version");
        String version = installedVersion();
        if (target.isDirectory() && stamp.exists()) {
            String installed = readAll(stamp);
            if (version.equals(installed)) return target;
        }
        deleteTree(target);
        copyAssetTree(getAssets(), "static", target);
        try (FileOutputStream out = new FileOutputStream(stamp)) {
            out.write(version.getBytes("UTF-8"));
        }
        return target;
    }

    private String installedVersion() {
        try {
            android.content.pm.PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
            long code = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P ? info.getLongVersionCode() : info.versionCode;
            return info.versionName + "/" + code;
        } catch (android.content.pm.PackageManager.NameNotFoundException error) {
            return "unknown";
        }
    }

    private static void copyAssetTree(AssetManager assets, String path, File into) throws IOException {
        String[] children = assets.list(path);
        if (children == null || children.length == 0) {
            copyAssetFile(assets, path, into);
            return;
        }
        if (!into.isDirectory() && !into.mkdirs()) {
            throw new IOException("Could not create " + into);
        }
        for (String child : children) {
            copyAssetTree(assets, path + "/" + child, new File(into, child));
        }
    }

    private static void copyAssetFile(AssetManager assets, String path, File into) throws IOException {
        File parent = into.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
            throw new IOException("Could not create " + parent);
        }
        try (InputStream in = assets.open(path); OutputStream out = new FileOutputStream(into)) {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) != -1) out.write(buffer, 0, read);
        }
    }

    private static void deleteTree(File file) {
        File[] children = file.listFiles();
        if (children != null) for (File child : children) deleteTree(child);
        //noinspection ResultOfMethodCallIgnored
        file.delete();
    }

    private static String readAll(File file) throws IOException {
        // InputStream.readAllBytes needs API 33; this runs down to API 24.
        try (InputStream in = new java.io.FileInputStream(file);
             java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int read;
            while ((read = in.read(buffer)) != -1) out.write(buffer, 0, read);
            return out.toString("UTF-8");
        }
    }

    private synchronized void acquireLocks() {
        lockWarnings.clear();
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        if (wifi != null) {
            try {
                wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "pitbox:telemetry");
                wifiLock.setReferenceCounted(false);
                wifiLock.acquire();
            } catch (RuntimeException error) {
                lockWarnings.add("Wi-Fi performance lock unavailable; keep the app visible during racing.");
                Log.w(TAG, "Wi-Fi lock unavailable", error);
            }
            try {
                multicastLock = wifi.createMulticastLock("pitbox:broadcast");
                multicastLock.setReferenceCounted(false);
                multicastLock.acquire();
            } catch (RuntimeException error) {
                lockWarnings.add("Multicast lock unavailable; use the tablet's IP with game UDP broadcast off.");
                Log.w(TAG, "Multicast lock unavailable", error);
            }
        }
        PowerManager power = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (power != null) {
            try {
                wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "pitbox:backend");
                wakeLock.setReferenceCounted(false);
                wakeLock.acquire();
            } catch (RuntimeException error) {
                lockWarnings.add("CPU wake lock unavailable; keep the app visible during racing.");
                Log.w(TAG, "Wake lock unavailable", error);
            }
        }
    }

    private synchronized void releaseLocks() {
        if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
        if (multicastLock != null && multicastLock.isHeld()) multicastLock.release();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        wifiLock = null;
        multicastLock = null;
        wakeLock = null;
    }

    private boolean microphoneGranted() {
        return checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, getString(R.string.notification_channel), NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Shown while Your Pit Box is receiving telemetry.");
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.createNotificationChannel(channel);
    }

    private void startInForeground() {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent openIntent = PendingIntent.getActivity(
                this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Intent stop = new Intent(this, PitBoxService.class).setAction(ACTION_STOP);
        PendingIntent stopIntent = PendingIntent.getService(
                this, 1, stop, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentTitle(getString(R.string.notification_title))
                .setContentText(getString(R.string.notification_text))
                .setContentIntent(openIntent)
                .addAction(0, getString(R.string.notification_stop), stopIntent)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .build();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Android 14 rejects a microphone-typed service without the
            // permission, so the type is added only once it is granted.
            int type = ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE;
            if (microphoneGranted()) type |= ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;
            startForeground(NOTIFICATION_ID, notification, type);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    @Override
    public void onDestroy() {
        stopBackend();
        releaseLocks();
        if (networkMonitor != null) networkMonitor.stop();
        if (instance == this) instance = null;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
