package com.yourpitbox.app;

/** Recognizes an edge reveal without treating ordinary dashboard taps as one. */
final class ImmersiveRehidePolicy {
    private boolean fromEdge;
    private boolean moved;
    private float startY;
    private float threshold;
    private boolean restoring;
    private boolean restoreArmed;

    static boolean canHide(boolean resumed, boolean focused, boolean keyboardVisible) {
        return resumed && focused && !keyboardVisible;
    }

    boolean beginRestore() {
        if (!canScheduleRestore()) return false;
        restoreArmed = false;
        restoring = true;
        return true;
    }

    // Only a new physical edge gesture grants a restore. Insets callbacks
    // from our own show/hide may arrive after finishRestore, so a transition
    // flag alone cannot prevent them from repeatedly flashing hidden bars.
    boolean canScheduleRestore() { return restoreArmed && !restoring; }

    void finishRestore() { restoring = false; }

    void deferRestore() {
        // Typing suspends a pending edge reveal rather than forgetting it.
        // Samsung can otherwise leave its transient status bar stuck after
        // the keyboard closes, even though the app still requests hidden bars.
        restoreArmed |= restoring;
        restoring = false;
    }

    void cancelRestore() {
        restoreArmed = false;
        restoring = false;
    }

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
        if (revealed && !restoring) restoreArmed = true;
        return revealed;
    }

    void reset() {
        fromEdge = false;
        moved = false;
    }
}
