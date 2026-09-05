#!/usr/bin/env bash
# Cross-compile the backend's Rust dependencies for Android.
#
# pydantic-core, jiter and rpds-py ship no Android wheels on PyPI and are not
# in Chaquopy's repository, so the APK build cannot pip-install them. This
# builds them once with cibuildwheel's Android support into android/wheels/,
# where app/build.gradle.kts points pip with --find-links.
#
# Needs: Python 3.11+ (3.12 recommended: 3.13's strict certificate checks
# reject some corporate proxies), the Android SDK with cmdline-tools in
# $ANDROID_HOME, a Rust toolchain on PATH (rustup adds the Android targets
# itself), and uv. Versions are pinned to what pyproject.toml resolves to on
# the desktop, because pydantic pins pydantic-core exactly.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/wheels"
WORK="${PITBOX_WHEEL_WORK:-$HERE/.wheel-build}"
PYTHON="${PITBOX_HOST_PYTHON:-python3}"

PACKAGES=(
  "pydantic-core 2.46.5"
  "jiter 0.16.0"
  "rpds-py 2026.6.3"
)

: "${ANDROID_HOME:?set ANDROID_HOME to the Android SDK}"
mkdir -p "$OUT" "$WORK/src"

export CIBW_PLATFORM=android
export CIBW_ARCHS="${CIBW_ARCHS:-arm64_v8a x86_64}"
export CIBW_BUILD="cp313-*"
export CIBW_BUILD_FRONTEND=uv

for entry in "${PACKAGES[@]}"; do
  set -- $entry
  name="$1"; version="$2"
  dist="${name//-/_}"
  if ls "$OUT"/"$dist"-"$version"-*.whl >/dev/null 2>&1; then
    echo "== $name $version: already built"
    continue
  fi
  echo "== $name $version"
  url="$(curl -sS "https://pypi.org/pypi/$name/$version/json" \
    | "$PYTHON" -c "import sys,json; print(next(u['url'] for u in json.load(sys.stdin)['urls'] if u['packagetype']=='sdist'))")"
  sdist="$WORK/src/$(basename "$url")"
  [ -f "$sdist" ] || curl -sS -L -o "$sdist" "$url"
  rm -rf "$WORK/$name" && mkdir -p "$WORK/$name"
  tar xzf "$sdist" -C "$WORK/$name"
  ( cd "$WORK/$name"/* && "$PYTHON" -m cibuildwheel --output-dir "$OUT" . )
done

echo
echo "Wheels in $OUT:"
ls -1 "$OUT"
