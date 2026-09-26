package com.yourpitbox.app;

/** Recognizes an edge reveal without treating ordinary dashboard taps as one. */
final class ImmersiveRehidePolicy {
    private boolean fromEdge;
    private boolean moved;
    private float startY;
    private float threshold;
    private boolean restoring;

    static boolean canHide(boolean resumed, boolean focused, boolean keyboardVisible) {
        return resumed && focused && !keyboardVisible;
    }

    boolean beginRestore() {
        if (restoring) return false;
        restoring = true;
        return true;
    }

    boolean canScheduleRestore() { return !restoring; }

    void finishRestore() { restoring = false; }

    void start(float y, int height, float edgeSize) {
        threshold = edgeSize;
        startY = y;
        moved = false;
        fromEdge = height > 0 && (y <= edgeSize || y >= height - edgeSize);
    }

    void move(float y) {
        moved |= Math.abs(y - startY) >= threshold;
    }

    boolean finish(boolean cancelled) {
        // Android cancels the app's touch stream when a system edge gesture
        // takes over, sometimes before delivering its first MOVE to the app.
        boolean revealed = fromEdge && (moved || cancelled);
        reset();
        return revealed;
    }

    void reset() {
        fromEdge = false;
        moved = false;
    }
}
