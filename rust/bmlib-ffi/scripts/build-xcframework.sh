#!/usr/bin/env bash
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# Build BmlibFFI.xcframework (dynamic frameworks by default) and the Swift
# bindings.
#
#   rust/bmlib-ffi/scripts/build-xcframework.sh <out-dir> [--static]
#
# Produces in <out-dir>:
#   BmlibFFI.xcframework/   ios-arm64, ios-arm64_x86_64-simulator,
#                           macos-arm64_x86_64
#   Swift/bmlib_ffi.swift   the generated bindings (compile into a Swift target
#                           that depends on the BmlibFFI binary target)
#
# Both are built in a staging directory and moved into <out-dir> only once the
# whole build has succeeded, so a failed run leaves the previous output intact.
#
# Dynamic frameworks by default: each architecture is a dead-stripped dylib of
# about 0.67 MB (the simulator and macOS slices hold two architectures each),
# where a static archive carries every dependency's objects and is only
# dead-stripped when the app links. That difference is what decides whether the
# xcframework can be committed to the consuming repository without LFS.
# `--static` builds the static-library variant for comparison.
#
# Every slice is built through with-remapped-paths.sh, so no builder's path is
# compiled in and two checkouts build the same bytes (#490).
#
# Needs: rustup with the five Apple targets below, Xcode's command-line tools
# (xcodebuild, lipo, install_name_tool, strip) and python3.
set -euo pipefail

usage="usage: build-xcframework.sh <out-dir> [--static]"
OUT=${1:?$usage}
case "${2:-}" in
  "") MODE=dynamic ;;
  --static) MODE=static ;;
  *) echo "$usage (got '$2')" >&2; exit 2 ;;
esac
[ $# -le 2 ] || { echo "$usage (too many arguments)" >&2; exit 2; }

HERE=$(cd "$(dirname "$0")/.." && pwd)          # rust/bmlib-ffi
WS=$(cd "$HERE/.." && pwd)                       # rust/
PROFILE=release-ffi
NAME=BmlibFFI
IOS_MIN=17.0
MACOS_MIN=14.0

# One read of the workspace: the version stamped into Info.plist, and the
# target directory cargo actually writes to (CARGO_TARGET_DIR or
# build.target-dir may move it from rust/target; reading a stale rust/target
# would package old slices under the current version).
META=$(cd "$WS" && cargo metadata --no-deps --format-version 1)
VERSION=$(printf '%s' "$META" | python3 -c \
  'import json,sys; print([p["version"] for p in json.load(sys.stdin)["packages"] if p["name"]=="bmlib-ffi"][0])')
TARGET_DIR=$(printf '%s' "$META" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["target_directory"])')

GEN=$(mktemp -d)
STAGE=$(mktemp -d)
trap 'rm -rf "$GEN" "$STAGE"' EXIT

export IPHONEOS_DEPLOYMENT_TARGET=$IOS_MIN
export MACOSX_DEPLOYMENT_TARGET=$MACOS_MIN
# rustc links a dylib with its absolute output path as the install name, and
# ld hashes that into LC_UUID, which install_name_tool -id below keeps. So a
# fixed name, else two checkouts differ by the UUID alone (#490). The longest
# of the final names, so install_name_tool always has room to rewrite it.
export CARGO_ENCODED_RUSTFLAGS="-Clink-arg=-Wl,-install_name,@rpath/$NAME.framework/Versions/A/$NAME"

# macOS is universal: an app's macOS archive builds arm64 *and* x86_64 (the
# default ARCHS), and a single-architecture slice fails its x86_64 link.
TARGETS=(aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios aarch64-apple-darwin x86_64-apple-darwin)
for t in "${TARGETS[@]}"; do
  # Cargo does not track the *_DEPLOYMENT_TARGET variables, so a cached link
  # keeps whatever minimum OS it was linked with. Relink bmlib-ffi (only). This
  # clean succeeds on a target never built, so a failure here is real.
  (cd "$WS" && cargo clean -p bmlib-ffi --profile "$PROFILE" --target "$t")
  (cd "$WS" && "$HERE/scripts/with-remapped-paths.sh" \
    cargo build -p bmlib-ffi --profile "$PROFILE" --target "$t")
done

# Bindings from an unstripped host build of the same source revision: library
# mode reads the UNIFFI_META_* metadata out of the binary, and the release-ffi
# slices are symbol-stripped. Both builds use the same UniFFI version, which is
# what the bindings' checksums compare.
(cd "$WS" && cargo build -p bmlib-ffi --features bindgen --lib --bins --release)
"$TARGET_DIR/release/uniffi-bindgen-swift" --swift-sources --headers --modulemap \
  --xcframework --module-name "$NAME" --modulemap-filename module.modulemap \
  "$TARGET_DIR/release/libbmlib_ffi.dylib" "$GEN"

lib() { echo "$TARGET_DIR/$1/$PROFILE/libbmlib_ffi.$2"; }

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

BUILT="$STAGE/out"
mkdir -p "$BUILT/Swift"
cp "$GEN/bmlib_ffi.swift" "$BUILT/Swift/"

if [ "$MODE" = static ]; then
  HDR="$STAGE/headers"; mkdir -p "$HDR"
  cp "$GEN/$NAME.h" "$HDR/"
  # A static library's modulemap is a plain (non-framework) module.
  sed 's/^framework module/module/' "$GEN/module.modulemap" > "$HDR/module.modulemap"
  cp "$(lib aarch64-apple-ios a)" "$STAGE/libios.a"
  lipo -create "$(lib aarch64-apple-ios-sim a)" "$(lib x86_64-apple-ios a)" -output "$STAGE/libsim.a"
  lipo -create "$(lib aarch64-apple-darwin a)" "$(lib x86_64-apple-darwin a)" -output "$STAGE/libmac.a"
  for f in "$STAGE/libios.a" "$STAGE/libsim.a" "$STAGE/libmac.a"; do
    xcrun strip -S -x "$f"
  done
  xcodebuild -create-xcframework \
    -library "$STAGE/libios.a" -headers "$HDR" \
    -library "$STAGE/libsim.a" -headers "$HDR" \
    -library "$STAGE/libmac.a" -headers "$HDR" \
    -output "$BUILT/$NAME.xcframework"
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
    -output "$BUILT/$NAME.xcframework"
fi

# xcodebuild lists the slices in no fixed order; sort them so two builds of one
# source write the same Info.plist.
python3 - "$BUILT/$NAME.xcframework/Info.plist" <<'PY'
import plistlib, sys
with open(sys.argv[1], "rb") as f:
    info = plistlib.load(f)
info["AvailableLibraries"].sort(key=lambda lib: lib["LibraryIdentifier"])
with open(sys.argv[1], "wb") as f:
    plistlib.dump(info, f)
PY

# Only now replace what <out-dir> held.
mkdir -p "$OUT"
rm -rf "${OUT:?}/$NAME.xcframework" "${OUT:?}/Swift"
mv "$BUILT/$NAME.xcframework" "$BUILT/Swift" "$OUT/"
echo "built $OUT/$NAME.xcframework ($MODE) from bmlib-ffi $VERSION"
du -sh "$OUT/$NAME.xcframework"
