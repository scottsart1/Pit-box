plugins { id("com.android.application") }

android {
    buildFeatures { buildConfig = true }
    namespace = "com.yourpitbox.nascar"
    compileSdk = 36
    defaultConfig {
        applicationId = "com.yourpitbox.nascar"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        manifestPlaceholders["nascarAppLabel"] = "YourPitBox NASCAR"
    }
    buildTypes {
        debug { applicationIdSuffix = ".debug"; versionNameSuffix = "-debug"; manifestPlaceholders["nascarAppLabel"] = "YourPitBox NASCAR QA" }
        release { isMinifyEnabled = false; isDebuggable = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
