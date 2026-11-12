package com.yourpitbox.app;

import android.app.Application;

/** Interpreter startup is deferred to the unlocked service worker thread so
 * the native opening animation can render immediately on a fresh install. */
public class PitBoxApplication extends Application {}
