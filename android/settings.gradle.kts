// Your Pit Box for Android: the same Python backend and dashboard as the
// desktop build, packaged as an APK. See android/README.md.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
    plugins {
        id("com.android.application") version "8.13.0"
        // Embeds CPython and pip-installs the backend's dependencies into the APK.
        id("com.chaquo.python") version "17.0.0"
    }
}

dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "YourPitBox"
include(":app")
