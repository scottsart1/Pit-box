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
val nativeConstraints = rootProject.layout.projectDirectory.file("native-requirements.txt")
// Read the shared engine's version so the APK can never advertise an older
// engine than the source which Chaquopy actually packages.
val backendVersion = Regex("__version__ = \"([^\"]+)\"")
    .find(rootProject.file("../src/pitwall/__init__.py").readText())!!.groupValues[1]
val androidRevision = 13
val signingPath = providers.environmentVariable("PITBOX_KEYSTORE_PATH").orNull

android {
    namespace = "com.yourpitbox.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.yourpitbox.app"
        minSdk = 24
        targetSdk = 35
        versionCode = androidRevision
        versionName = "$backendVersion-android.$androidRevision"
        ndk {
            // 64-bit phones and tablets, plus the x86_64 emulator.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        if (!signingPath.isNullOrBlank()) {
            create("distribution") {
                storeFile = file(signingPath)
                storePassword = providers.environmentVariable("PITBOX_KEYSTORE_PASSWORD").get()
                keyAlias = providers.environmentVariable("PITBOX_KEY_ALIAS").get()
                keyPassword = providers.environmentVariable("PITBOX_KEY_PASSWORD").get()
            }
        }
    }

    buildTypes {
        debug {
            // CI debug certificates change between clean runners. Keep this
            // test application separate from the user's installed app/data.
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            isMinifyEnabled = false
            if (!signingPath.isNullOrBlank()) {
                signingConfig = signingConfigs.getByName("distribution")
            }
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

val copyDashboard by tasks.registering(Sync::class) {
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
            // A native dependency update without a matching Android wheel
            // must not silently pick a newer, unbuildable source release.
            options("--constraint", nativeConstraints.asFile.absolutePath)
            // The runtime dependencies from pyproject.toml, minus what cannot
            // run on Android: sounddevice/soundfile (PortAudio; replaced by
            // the sounddevice.py and soundfile.py in src/main/python) and
            // uvicorn's [standard] extras (uvloop, httptools), which have no
            // Android builds. numpy is the newest Chaquopy provides for
            // Python 3.13; the test suite passes on it.
            install("fastapi>=0.115")
            install("uvicorn>=0.30")
            install("wsproto")
            // The OpenAI Realtime radio (speech-to-speech mode) connects with
            // websockets; cross-compiled like the Rust packages.
            install("websockets")
            install("openai>=2.45,<3")
            // Match desktop proxy support; SOCKS needs this pure-Python extra.
            install("httpx[socks]>=0.28")
            install("pydantic>=2.8")
            install("pydantic-settings>=2.4")
            install("python-multipart>=0.0.9")
            install("f1-packets>=2026.1.1,<2027")
            install("numpy==1.26.2")
            install("jsonschema>=4.23")
            // Chaquopy publishes cp313 wheels for both supported ABIs.
            install("cryptography==42.0.8")
            // SVG pairing codes use the pure-Python renderer, without Pillow.
            install("qrcode>=8")
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
