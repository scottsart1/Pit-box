pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
    plugins { id("com.android.application") version "8.13.0" }
}
dependencyResolutionManagement { repositories { google(); mavenCentral() } }
rootProject.name = "YourPitBoxNASCAR"
include(":app")
