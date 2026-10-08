package com.yourpitbox.app;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/** Extract the installed APK's dashboard without touching saved sessions or settings. */
final class DashboardAssets {
    interface Source {
        String[] list(String path) throws IOException;
        InputStream open(String path) throws IOException;
    }

    static String installationStamp(String versionName, long versionCode, long lastUpdateTime) {
        return versionName + "/" + versionCode + "/" + lastUpdateTime;
    }

    static File install(File files, String installation, Source source) throws IOException {
        File target = new File(files, "static");
        File stamp = new File(files, "static.version");
        if (target.isDirectory() && stamp.isFile() && installation.equals(readAll(stamp))) {
            return target;
        }
        // Invalidate before replacing any files: a failed extraction must be
        // retried even if this installation previously had a completed cache.
        if (stamp.exists() && !stamp.delete()) throw new IOException("Could not remove " + stamp);
        deleteTree(target);
        copyTree(source, "static", target);
        try (OutputStream out = new FileOutputStream(stamp)) {
            out.write(installation.getBytes("UTF-8"));
        }
        return target;
    }

    private static void copyTree(Source source, String path, File into) throws IOException {
        String[] children = source.list(path);
        if (children != null && children.length > 0) {
            if (!into.isDirectory() && !into.mkdirs()) throw new IOException("Could not create " + into);
            for (String child : children) copyTree(source, path + "/" + child, new File(into, child));
            return;
        }
        File parent = into.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
            throw new IOException("Could not create " + parent);
        }
        try (InputStream in = source.open(path); OutputStream out = new FileOutputStream(into)) {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) != -1) out.write(buffer, 0, read);
        }
    }

    private static void deleteTree(File file) throws IOException {
        if (!file.exists()) return;
        File[] children = file.listFiles();
        if (children != null) for (File child : children) deleteTree(child);
        if (!file.delete()) throw new IOException("Could not remove " + file);
    }

    private static String readAll(File file) throws IOException {
        // InputStream.readAllBytes needs API 33; the app supports API 24.
        try (InputStream in = new FileInputStream(file);
             ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int read;
            while ((read = in.read(buffer)) != -1) out.write(buffer, 0, read);
            return out.toString("UTF-8");
        }
    }
}
