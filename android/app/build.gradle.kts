import com.android.build.api.dsl.ApplicationExtension

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// The Python that runs the build must be the same minor version as the one
// embedded in the app. Override with -Ppitbox.buildPython=/path/to/python3.13.
val buildPythonCommand: String = (project.findProperty("pitbox.buildPython") as String?) ?: "python3.13"

// pydantic-core, jiter and rpds-py are Rust extensions with no Android wheels
// on PyPI or in Chaquopy's repository. android/build-wheels.sh cross-compiles
// them into this directory with cibuildwheel; pip then finds them here.
val localWheels = rootProject.layout.projectDirectory.dir("wheels")

android {
    namespace = "com.yourpitbox.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.yourpitbox.app"
        minSdk = 24
        targetSdk = 35
        versionCode = 2
        versionName = "4.9.1-android.2"
        ndk {
            // 64-bit phones and tablets, plus the x86_64 emulator.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    // The dashboard (repo static/) ships as an asset tree under assets/static
    // and is copied to the app's files directory on first start, because the
    // backend serves it with StaticFiles, which needs a real directory.
    sourceSets {
        getByName("main") {
            assets.srcDirs(layout.buildDirectory.dir("generated/pitbox-assets"))
        }
    }
}

val copyDashboard by tasks.registering(Copy::class) {
    from(rootProject.layout.projectDirectory.dir("../static"))
    into(layout.buildDirectory.dir("generated/pitbox-assets/static"))
}
tasks.named("preBuild") { dependsOn(copyDashboard) }

chaquopy {
    defaultConfig {
        version = "3.13"
        buildPython(buildPythonCommand)

        pip {
            options("--find-links", localWheels.asFile.absolutePath)
            // The runtime dependencies from pyproject.toml, minus what cannot
            // run on Android: sounddevice/soundfile (PortAudio; voice is
            // phase 2) and uvicorn's [standard] extras (uvloop, httptools,
            // websockets), which have no Android builds. wsproto replaces
            // websockets for the dashboard's /ws stream. numpy is the newest
            // Chaquopy provides for Python 3.13; the test suite passes on it.
            install("fastapi>=0.115")
            install("uvicorn>=0.30")
            install("wsproto")
            install("openai>=2.45,<3")
            install("pydantic>=2.8")
            install("pydantic-settings>=2.4")
            install("python-multipart>=0.0.9")
            install("f1-packets>=2026.1.1,<2027")
            install("numpy==1.26.2")
            install("jsonschema>=4.23")
        }
    }

    sourceSets {
        getByName("main") {
            // The backend, straight from the repository. Nothing is copied or
            // forked: src/pitwall is the same package the desktop app runs.
            srcDir("../../src")
            exclude("**/__pycache__/**")
        }
    }
}

dependencies {
    implementation("androidx.core:core:1.13.1")
}
