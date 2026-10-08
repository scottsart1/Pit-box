"""Execute dashboard extraction through updates and failed installs, with real files."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_apk_reinstalls_refresh_assets_preserve_history_and_retry_failed_extraction(tmp_path):
    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        pytest.skip("Java toolchain is required for the executable dashboard extraction check")
    harness = tmp_path / "DashboardAssetsCheck.java"
    harness.write_text('''
package com.yourpitbox.app;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public final class DashboardAssetsCheck {
    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    private static final class Assets implements DashboardAssets.Source {
        final Map<String, String> files = new LinkedHashMap<>();
        String failPath;
        int reads;
        public String[] list(String path) {
            Set<String> children = new LinkedHashSet<>();
            String prefix = path + "/";
            for (String name : files.keySet()) {
                if (name.startsWith(prefix)) children.add(name.substring(prefix.length()).split("/", 2)[0]);
            }
            return children.toArray(new String[0]);
        }
        public InputStream open(String path) throws IOException {
            reads++;
            if (path.equals(failPath)) throw new IOException("interrupted APK extraction");
            if (!files.containsKey(path)) throw new FileNotFoundException(path);
            return new ByteArrayInputStream(files.get(path).getBytes(StandardCharsets.UTF_8));
        }
    }
    public static void main(String[] args) throws Exception {
        Path root = Paths.get(args[0]);
        Files.createDirectories(root.resolve("PitWallData"));
        Path history = root.resolve("PitWallData/history.db");
        Files.writeString(history, "saved sessions and settings");
        Assets assets = new Assets();
        assets.files.put("static/index.html", "first dashboard");
        assets.files.put("static/js/app.js", "first script");
        assets.files.put("static/old.js", "obsolete script");
        String original = DashboardAssets.installationStamp("5.3.2", 34, 1000);
        DashboardAssets.install(root.toFile(), original, assets);
        require(Files.readString(root.resolve("static/index.html")).equals("first dashboard"), "first install");
        int reads = assets.reads;
        DashboardAssets.install(root.toFile(), original, assets);
        require(assets.reads == reads, "ordinary launches reuse the completed extraction");

        // Same public version, different APK install: all served bytes must refresh.
        String rebuild = DashboardAssets.installationStamp("5.3.2", 34, 2000);
        assets.files.put("static/index.html", "rebuilt dashboard");
        assets.files.put("static/js/app.js", "rebuilt script");
        assets.files.remove("static/old.js");
        DashboardAssets.install(root.toFile(), rebuild, assets);
        require(Files.readString(root.resolve("static/index.html")).equals("rebuilt dashboard"), "same-version HTML update");
        require(Files.readString(root.resolve("static/js/app.js")).equals("rebuilt script"), "nested JavaScript update");
        require(!Files.exists(root.resolve("static/old.js")), "removed APK assets do not survive");
        require(Files.readString(history).equals("saved sessions and settings"), "session history survives reinstall");

        // Existing installations use the old version-only stamp; migrate on first launch.
        Files.writeString(root.resolve("static.version"), "5.3.2/34");
        assets.files.put("static/index.html", "migrated dashboard");
        DashboardAssets.install(root.toFile(), rebuild, assets);
        require(Files.readString(root.resolve("static/index.html")).equals("migrated dashboard"), "legacy cache invalidated");

        String failedUpdate = DashboardAssets.installationStamp("5.3.2", 34, 3000);
        assets.files.put("static/index.html", "third dashboard");
        assets.files.put("static/js/app.js", "third script");
        assets.failPath = "static/js/app.js";
        try {
            DashboardAssets.install(root.toFile(), failedUpdate, assets);
            throw new AssertionError("interrupted extraction was reported successful");
        } catch (IOException expected) { }
        require(!Files.exists(root.resolve("static.version")), "incomplete extraction is never cached");
        assets.failPath = null;
        DashboardAssets.install(root.toFile(), failedUpdate, assets);
        require(Files.readString(root.resolve("static/js/app.js")).equals("third script"), "next launch repairs incomplete assets");
        require(Files.readString(history).equals("saved sessions and settings"), "failed upgrade preserves saved data");
    }
}
''', encoding="utf-8")
    source = ROOT / "android/app/src/main/java/com/yourpitbox/app/DashboardAssets.java"
    subprocess.run([javac, "-d", str(tmp_path), str(source), str(harness)], check=True, capture_output=True)
    subprocess.run([java, "-cp", str(tmp_path), "com.yourpitbox.app.DashboardAssetsCheck", str(tmp_path / "files")],
                   check=True, capture_output=True)
