# bmlib-ffi — UniFFI bindings for the mobile apps (SPIKE)

`bmlib-ffi` exposes part of the [`bmlib`](../bmlib) crate to Swift (iOS/macOS)
and Kotlin (Android) through [UniFFI](https://mozilla.github.io/uniffi-rs/).
It exists for bmlibrarian_lite's WP-2 spike: can the apps call bmlib's Rust
instead of hand-porting each bmlib fix twice? The measured answer and the
recommendation are in bmlibrarian_lite's
`doc/cross_platform/bmlib_sync/uniffi_spike.md`.

**Not published** (`publish = false`), and not production code.

## What it exposes

| Item | Kind | Notes |
|---|---|---|
| `parse_jats(xml) -> JatsArticle` | function | bmlib's JATS reader. Throws `JatsError.Xml { reason }` on malformed XML |
| `parse_jats_audited(xml, known_pmc_id) -> JatsParseReport` | function | The article plus the audit: `warnings` (publisher losses) and `diagnostics` (reader defects; empty on a correct reader) |
| `bmlib_version() -> String` | function | The crate version the binary was built from |
| `JatsArticle` and its parts | records | Owned FFI copies of `bmlib::fulltext::models`, mapped field for field in `src/lib.rs`. Tables are HTML (`html_content`), as in bmlib |
| `ForeignHttpClient` | foreign trait | **Sketch.** The app's transport (URLSession / OkHttp), adapted to bmlib's `HttpClient` |
| `BmlibFullTextService` | object | **Sketch.** bmlib's `FullTextService` over a `ForeignHttpClient`; only `fetch_status` is exported, to prove the callback round trip compiles |

Error fields are named `reason`, never `message`: UniFFI makes an error a
Kotlin exception, and a `message` field collides with `Throwable.message`.

## Features and profile

- `bmlib` is a dependency **without default features**, so neither the
  bundled SQLite (`sqlite`) nor `ureq`/rustls (`http-ureq`) is compiled. The
  apps own their storage and their transport.
- `full` turns both back on. Nothing exported needs them; it exists so the
  saving can be measured.
- `bindgen` builds the two generator binaries (`uniffi-bindgen`,
  `uniffi-bindgen-swift`).
- Ship with `--profile release-ffi` (workspace `Cargo.toml`): fat LTO,
  `opt-level = "s"`, symbols stripped. `panic` stays `unwind` so UniFFI turns a
  Rust panic into a foreign error instead of a crash.

UniFFI is pinned exactly (`=0.32.2`): the generated bindings check the
library's checksums at first call, so the bindings, the binary and the UniFFI
version must always come from one build.

## Building

Run everything from `rust/`.

### Prerequisites

```bash
rustup target add aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios \
                  aarch64-apple-darwin x86_64-apple-darwin          # Apple
rustup target add aarch64-linux-android x86_64-linux-android        # Android
cargo install cargo-ndk                                              # Android
export ANDROID_NDK_HOME=~/Library/Android/sdk/ndk/<version>          # Android
```

### Apple: xcframework + Swift bindings

```bash
bmlib-ffi/scripts/build-xcframework.sh <out-dir>            # dynamic frameworks (default)
bmlib-ffi/scripts/build-xcframework.sh <out-dir> --static   # static libraries, for comparison
```

Writes `<out-dir>/BmlibFFI.xcframework` (slices `ios-arm64`,
`ios-arm64_x86_64-simulator`, `macos-arm64_x86_64`; minimum iOS 17.0, macOS
14.0) and `<out-dir>/Swift/bmlib_ffi.swift`. A consumer adds the xcframework as
a `binaryTarget` named `BmlibFFI` and compiles the Swift file in a target that
depends on it (bmlibrarian_lite: `Packages/BioMedLit`, target `BmlibCore`).

The macOS slice is universal because an app's macOS archive builds arm64 and
x86_64; an arm64-only slice fails the x86_64 link.

Dynamic frameworks by default, because the committed artifact differs by an
order of magnitude: 3.3 MB on disk (~0.8 MB packed in git) against 39 MB
(6.9 MB packed) for the stripped static archives, which carry every
dependency's objects until the app's linker dead-strips them.

### Android: .so files + Kotlin bindings

```bash
cargo ndk -t arm64-v8a -t x86_64 -p 26 -o <jniLibs-dir> \
    build -p bmlib-ffi --profile release-ffi

cargo build -p bmlib-ffi --features bindgen --lib --bins --release
./target/release/uniffi-bindgen generate --no-format \
    --library target/release/libbmlib_ffi.dylib --language kotlin --out-dir <dir>
```

Kotlin settings (package `org.bmlib.ffi`, Android cleaner) are in
`uniffi.toml`. The bindings use JNA: an app depends on
`net.java.dev.jna:jna:<v>@aar`, JVM unit tests on the plain JAR, with the host
build of this crate on `jna.library.path`. bmlibrarian_lite's
`app/build.gradle.kts` runs both cargo steps as Gradle tasks when it is given a
bmlib checkout (`bmlib.dir` / `BMLIB_DIR`).

### Bindings from the host build

`uniffi-bindgen` and `uniffi-bindgen-swift` read the UniFFI metadata out of a
built library ("library mode"). Use the unstripped host build
(`target/release/libbmlib_ffi.dylib`); the `release-ffi` slices are
symbol-stripped.

### Trying it from Python

The same generator writes Python bindings, which is the quickest way to look at
what the reader returns without building an app:

```bash
./target/release/uniffi-bindgen generate --library target/release/libbmlib_ffi.dylib \
    --language python --out-dir /tmp/py && cp target/release/libbmlib_ffi.dylib /tmp/py/
python3 -c 'import sys; sys.path.insert(0, "/tmp/py"); import bmlib_ffi; \
    print(bmlib_ffi.parse_jats(open("article.xml").read()).title)'
```

## Sizes (measured 2026-10-17, release-ffi, stripped)

| Slice | Dynamic library | Static archive (stripped) |
|---|---|---|
| iOS arm64 | 0.67 MB (0.72 MB with `full`) | 10.4 MB (16.2 MB with `full`) |
| iOS simulator arm64 | 0.67 MB | 10.4 MB |
| iOS simulator x86_64 | 0.64 MB | 10.1 MB |
| macOS arm64 | 0.67 MB | 10.4 MB |
| macOS x86_64 | 0.64 MB | — |
| Android arm64-v8a | 0.82 MB (0.89 MB with `full`) | — |
| Android x86_64 | 0.87 MB (0.94 MB with `full`) | — |

Linked statically into a stub iOS app with `-dead_strip`, the library adds
0.65 MB (0.71 MB with `full`), so the trim's effect on the shipped app is
~50 KB; what it really saves is the C (SQLite) and assembly (ring) cross-builds
and 6 MB per static slice.
