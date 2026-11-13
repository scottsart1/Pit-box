package com.yourpitbox.app;

import android.content.Context;
import android.os.Build;
import android.os.SystemClock;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.IOException;

/**
 * Work every driver profile needs, done while the profile screen is open.
 *
 * The interpreter starts and the backend's libraries are imported in the
 * background during the opening animation and while the driver types, so
 * opening a profile leaves only that profile's own settings, keys and
 * history to load. Nothing here reads a profile's data or decrypts a key,
 * so a driver who adds a new profile instead needs nothing undone.
 */
final class BackendWarmup {
    private static final String TAG = "PitBoxWarmup";
    private static boolean started;

    private BackendWarmup() {}

    /** Starts the interpreter once per process, on whichever thread asks first. */
    static synchronized void ensurePython(Context context) {
        if (!Python.isStarted()) Python.start(new AndroidPlatform(context.getApplicationContext()));
    }

    /** Begins warming in the background; later calls do nothing. */
    static synchronized void start(Context context) {
        if (started) return;
        started = true;
        Context app = context.getApplicationContext();
        new Thread(() -> {
            long began = SystemClock.elapsedRealtime();
            try {
                // The first profile always owns files/, so after an update its
                // dashboard copy can be refreshed before anyone signs in.
                installDashboard(app, app.getFilesDir());
            } catch (IOException | RuntimeException error) {
                Log.w(TAG, "Dashboard copy deferred: " + error.getClass().getSimpleName());
            }
            long assets = SystemClock.elapsedRealtime();
            try {
                ensurePython(app);
                Python.getInstance().getModule("pitbox_android").callAttr("warm_up");
            } catch (Throwable error) {
                // The backend starts normally without it, just more slowly.
                Log.w(TAG, "Warm-up skipped: " + error.getClass().getSimpleName());
            }
            long done = SystemClock.elapsedRealtime();
            Log.i(TAG, "Warmed in " + (done - began) + " ms (dashboard " + (assets - began)
                    + " ms, interpreter and libraries " + (done - assets) + " ms)");
        }, "pitbox-warmup").start();
    }

    /**
     * Copies the dashboard out of the APK's assets so the backend can serve it
     * from a real directory. Refreshed on every APK install, including a
     * rebuilt candidate with the same version, so JavaScript matches the backend.
     */
    static File installDashboard(Context context, File files) throws IOException {
        return DashboardAssets.install(files, installedVersion(context), new DashboardAssets.Source() {
            public String[] list(String path) throws IOException { return context.getAssets().list(path); }
            public java.io.InputStream open(String path) throws IOException { return context.getAssets().open(path); }
        });
    }

    private static String installedVersion(Context context) {
        try {
            android.content.pm.PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
            long code = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P ? info.getLongVersionCode() : info.versionCode;
            return DashboardAssets.installationStamp(info.versionName, code, info.lastUpdateTime);
        } catch (android.content.pm.PackageManager.NameNotFoundException error) {
            return "unknown";
        }
    }
}
