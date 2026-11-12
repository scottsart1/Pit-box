package com.yourpitbox.app;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.provider.Settings;
import android.view.View;

/** Small native launch mask: no network asset, WebView or Python dependency. */
public final class PitLaneIntro extends View {
    public static final long DURATION_MS = 2600;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private ValueAnimator animator;
    private final String tagline;
    private float progress;
    public PitLaneIntro(Context context) {
        super(context);
        setBackgroundColor(Color.rgb(7, 11, 15));
        tagline = context.getString(R.string.intro_tagline);
        setContentDescription(context.getString(R.string.intro_description));
        setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        // A cover, not a control: touches never reach the screen beneath it.
        setClickable(true);
    }
    @Override protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        float scale = Settings.Global.getFloat(getContext().getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1);
        if (scale == 0) { progress = 1; invalidate(); return; }
        animator = ValueAnimator.ofFloat(0, 1);
        animator.setDuration(DURATION_MS);
        animator.addUpdateListener(value -> { progress = (float) value.getAnimatedValue(); invalidate(); });
        animator.start();
    }
    @Override protected void onDetachedFromWindow() {
        if (animator != null) animator.cancel();
        super.onDetachedFromWindow();
    }
    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float d = getResources().getDisplayMetrics().density;
        float cx = getWidth() / 2f, cy = getHeight() / 2f;
        float width = Math.min(getWidth() - 56 * d, 360 * d);
        paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(d);
        paint.setColor(Color.rgb(38, 53, 66));
        canvas.drawLine(cx - width / 2, cy + 60 * d, cx + width / 2, cy + 60 * d, paint);
        paint.setStyle(Paint.Style.FILL);
        for (int i = 0; i < 5; i++) {
            boolean on = progress > .12f * (i + 1);
            paint.setColor(progress > .82f ? Color.rgb(73, 209, 125) : on ? Color.rgb(255, 105, 105) : Color.rgb(38, 53, 66));
            canvas.drawCircle(cx + (i - 2) * 25 * d, cy - 70 * d, 5 * d, paint);
        }
        paint.setTextAlign(Paint.Align.CENTER);
        paint.setTypeface(Typeface.create("sans-serif-black", Typeface.BOLD));
        paint.setTextSize(Math.min(35 * d, width / 8.3f));
        paint.setColor(Color.rgb(239, 246, 250)); canvas.drawText("YOUR PIT BOX", cx, cy, paint);
        paint.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        paint.setTextSize(11 * d); paint.setColor(Color.rgb(158, 178, 194));
        canvas.drawText(tagline, cx, cy + 27 * d, paint);
        paint.setColor(Color.rgb(73, 209, 125));
        canvas.drawRect(cx - width / 2, cy + 59 * d, cx - width / 2 + width * progress, cy + 61 * d, paint);
    }
}
