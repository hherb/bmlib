#!/usr/bin/env bash
# Build BmlibFFI.xcframework (dynamic frameworks) and the Swift bindings.
#
#   rust/bmlib-ffi/scripts/build-xcframework.sh <out-dir> [--static]
#
# Produces in <out-dir>:
#   BmlibFFI.xcframework/   ios-arm64, ios-arm64_x86_64-simulator,
#                           macos-arm64_x86_64
#   Swift/bmlib_ffi.swift   the generated bindings (compile into a Swift target
#                           that depends on the BmlibFFI binary target)
#
# Dynamic frameworks by default: each slice is the dead-stripped dylib
# (~0.67 MB), where a static archive carries every dependency's objects
# (~10 MB stripped, ~48 MB unstripped) and is only dead-stripped when the app
# links. That difference is what decides whether the xcframework can be
# committed to the consuming repository without LFS. `--static` builds the
# static-library variant for comparison.
set -euo pipefail

OUT=${1:?usage: build-xcframework.sh <out-dir> [--static]}
MODE=${2:-dynamic}
HERE=$(cd "$(dirname "$0")/.." && pwd)          # rust/bmlib-ffi
WS=$(cd "$HERE/.." && pwd)                       # rust/
PROFILE=release-ffi
NAME=BmlibFFI
IOS_MIN=17.0
MACOS_MIN=14.0
VERSION=$(cd "$WS" && cargo metadata --no-deps --format-version 1 \
  | python3 -c 'import json,sys; print([p["version"] for p in json.load(sys.stdin)["packages"] if p["name"]=="bmlib-ffi"][0])')

export IPHONEOS_DEPLOYMENT_TARGET=$IOS_MIN
export MACOSX_DEPLOYMENT_TARGET=$MACOS_MIN

# macOS is universal: an app's macOS archive builds arm64 *and* x86_64 (the
# default ARCHS), and a single-architecture slice fails its x86_64 link.
TARGETS=(aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios aarch64-apple-darwin x86_64-apple-darwin)
for t in "${TARGETS[@]}"; do
  # Cargo does not track the *_DEPLOYMENT_TARGET variables, so a cached link
  # keeps whatever minimum OS it was linked with. Relink bmlib-ffi (only).
  (cd "$WS" && cargo clean -p bmlib-ffi --profile "$PROFILE" --target "$t" 2>/dev/null || true)
  (cd "$WS" && cargo build -p bmlib-ffi --profile "$PROFILE" --target "$t")
done

# Bindings from the unstripped host build of the same source: library mode reads
# the UNIFFI_META_* metadata out of the binary, and the release-ffi slices are
# symbol-stripped.
(cd "$WS" && cargo build -p bmlib-ffi --features bindgen --lib --bins --release)
GEN=$(mktemp -d)
"$WS/target/release/uniffi-bindgen-swift" --swift-sources --headers --modulemap \
  --xcframework --module-name "$NAME" --modulemap-filename module.modulemap \
  "$WS/target/release/libbmlib_ffi.dylib" "$GEN"

rm -rf "$OUT/$NAME.xcframework" "$OUT/Swift"
mkdir -p "$OUT/Swift"
cp "$GEN/bmlib_ffi.swift" "$OUT/Swift/"

STAGE=$(mktemp -d)
lib() { echo "$WS/target/$1/$PROFILE/libbmlib_ffi.$2"; }

info_plist() { # <platform> <min-os>
  local platform=$1 minos=$2 minkey=MinimumOSVersion
  [ "$platform" = MacOSX ] && minkey=LSMinimumSystemVersion
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>org.bmlib.$NAME</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundlePackageType</key><string>FMWK</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleSupportedPlatforms</key><array><string>$platform</string></array>
  <key>$minkey</key><string>$minos</string>
</dict>
</plist>
EOF
}

# iOS-style (shallow) framework.
shallow_framework() { # <dir> <dylib> <platform>
  local fw="$1/$NAME.framework"
  mkdir -p "$fw/Headers" "$fw/Modules"
  cp "$2" "$fw/$NAME"
  install_name_tool -id "@rpath/$NAME.framework/$NAME" "$fw/$NAME"
  cp "$GEN/$NAME.h" "$fw/Headers/"
  cp "$GEN/module.modulemap" "$fw/Modules/"
  info_plist "$3" "$IOS_MIN" > "$fw/Info.plist"
}

# macOS requires the versioned (deep) bundle layout.
deep_framework() { # <dir> <dylib>
  local fw="$1/$NAME.framework" v="$1/$NAME.framework/Versions/A"
  mkdir -p "$v/Headers" "$v/Modules" "$v/Resources"
  cp "$2" "$v/$NAME"
  install_name_tool -id "@rpath/$NAME.framework/Versions/A/$NAME" "$v/$NAME"
  cp "$GEN/$NAME.h" "$v/Headers/"
  cp "$GEN/module.modulemap" "$v/Modules/"
  info_plist MacOSX "$MACOS_MIN" > "$v/Resources/Info.plist"
  ln -s A "$fw/Versions/Current"
  for item in "$NAME" Headers Modules Resources; do
    ln -s "Versions/Current/$item" "$fw/$item"
  done
}

if [ "$MODE" = "--static" ]; then
  HDR="$STAGE/headers"; mkdir -p "$HDR"
  cp "$GEN/$NAME.h" "$HDR/"
  # A static library's modulemap is a plain (non-framework) module.
  sed 's/^framework module/module/' "$GEN/module.modulemap" > "$HDR/module.modulemap"
  lipo -create "$(lib aarch64-apple-ios-sim a)" "$(lib x86_64-apple-ios a)" -output "$STAGE/libsim.a"
  lipo -create "$(lib aarch64-apple-darwin a)" "$(lib x86_64-apple-darwin a)" -output "$STAGE/libmac.a"
  for f in "$(lib aarch64-apple-ios a)" "$STAGE/libsim.a" "$STAGE/libmac.a"; do
    xcrun strip -S -x "$f" 2>/dev/null || true
  done
  xcodebuild -create-xcframework \
    -library "$(lib aarch64-apple-ios a)" -headers "$HDR" \
    -library "$STAGE/libsim.a" -headers "$HDR" \
    -library "$STAGE/libmac.a" -headers "$HDR" \
    -output "$OUT/$NAME.xcframework"
else
  mkdir -p "$STAGE/ios" "$STAGE/sim" "$STAGE/macos"
  lipo -create "$(lib aarch64-apple-ios-sim dylib)" "$(lib x86_64-apple-ios dylib)" \
    -output "$STAGE/libsim.dylib"
  lipo -create "$(lib aarch64-apple-darwin dylib)" "$(lib x86_64-apple-darwin dylib)" \
    -output "$STAGE/libmac.dylib"
  shallow_framework "$STAGE/ios" "$(lib aarch64-apple-ios dylib)" iPhoneOS
  shallow_framework "$STAGE/sim" "$STAGE/libsim.dylib" iPhoneSimulator
  deep_framework "$STAGE/macos" "$STAGE/libmac.dylib"
  xcodebuild -create-xcframework \
    -framework "$STAGE/ios/$NAME.framework" \
    -framework "$STAGE/sim/$NAME.framework" \
    -framework "$STAGE/macos/$NAME.framework" \
    -output "$OUT/$NAME.xcframework"
fi

rm -rf "$STAGE" "$GEN"
echo "built $OUT/$NAME.xcframework ($MODE) from bmlib-ffi $VERSION"
du -sh "$OUT/$NAME.xcframework"
