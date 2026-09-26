"""Execute the native edge-gesture policy against dashboard/OS event sequences."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_native_rehide_only_follows_edge_swipes_and_requires_a_safe_window(tmp_path):
    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        pytest.skip("Java toolchain is required for the executable native policy check")
    harness = tmp_path / "FullscreenPolicyCheck.java"
    harness.write_text('''
package com.yourpitbox.app;
public final class FullscreenPolicyCheck {
    private static void require(boolean condition, String name) {
        if (!condition) throw new AssertionError(name);
    }
    public static void main(String[] args) {
        ImmersiveRehidePolicy p = new ImmersiveRehidePolicy();
        p.start(1, 1800, 24); p.move(200);
        require(p.finish(false), "top-edge swipe");
        p.start(1799, 1800, 24); p.move(1600);
        require(p.finish(false), "bottom-edge swipe");
        p.start(1, 1800, 24);
        require(p.finish(true), "system steals edge before app gets MOVE");
        p.start(1, 1800, 24);
        require(!p.finish(false), "ordinary top-edge tap does not flash bars");
        p.start(800, 1800, 24); p.move(100);
        require(!p.finish(false), "dashboard scroll does not rehide");
        p.start(800, 1800, 24);
        require(!p.finish(true), "cancelled widget drag does not rehide");
        p.start(1, 1800, 24); p.move(100); p.reset();
        require(!p.finish(true), "pause/keyboard cancels pending gesture");
        require(!p.finish(true), "completed gesture is consumed once");
        p.start(1, 0, 24); p.move(100);
        require(!p.finish(true), "unmeasured window cannot trigger a gesture");
        p.cancelRestore();
        require(!p.canScheduleRestore() && !p.beginRestore(), "insets alone cannot trigger a restore");
        p.start(1, 1800, 24); p.move(200); p.finish(false);
        require(p.canScheduleRestore() && p.beginRestore(), "edge reveal grants one restore");
        require(!p.canScheduleRestore(), "explicit show insets cannot schedule another timer");
        require(!p.beginRestore(), "restore cannot reenter during show/hide");
        p.finishRestore();
        require(!p.canScheduleRestore() && !p.beginRestore(), "late visible insets cannot re-arm restore");
        p.start(1, 1800, 24); p.finish(false);
        require(!p.canScheduleRestore(), "ordinary tap after restore cannot flash hidden bars");
        p.start(1799, 1800, 24); p.move(1600); p.finish(false);
        require(p.canScheduleRestore(), "a future edge gesture may restore once");
        require(p.beginRestore(), "next independent restore starts");
        p.cancelRestore();
        require(!p.canScheduleRestore(), "cancellation consumes pending restore");
        p.start(1, 1800, 24); p.move(200); p.finish(false);
        require(p.canScheduleRestore(), "edge gesture after cancellation may restore");
        for (int state = 0; state < 8; state++) {
            boolean resumed = (state & 1) != 0;
            boolean focused = (state & 2) != 0;
            boolean keyboard = (state & 4) != 0;
            require(ImmersiveRehidePolicy.canHide(resumed, focused, keyboard) == (state == 3),
                    "window lifecycle/IME safety " + state);
        }
    }
}
''', encoding="utf-8")
    source = ROOT / "android/app/src/main/java/com/yourpitbox/app/ImmersiveRehidePolicy.java"
    subprocess.run([javac, "-d", str(tmp_path), str(source), str(harness)], check=True, capture_output=True)
    subprocess.run([java, "-cp", str(tmp_path), "com.yourpitbox.app.FullscreenPolicyCheck"],
                   check=True, capture_output=True)
