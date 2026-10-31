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
| `parse_jats(xml) -> JatsArticle` | function | bmlib's JATS reader. **Discards the audit**: its lines go only to stderr, which an app does not see. Throws `JatsError` |
| `parse_jats_audited(xml, known_pmc_id) -> JatsParseReport` | function | The article plus the audit: `warnings` (publisher losses), `diagnostics` (reader defects; empty on a correct reader) and `article_label` (the article's identifiers, to prefix them with). `known_pmc_id` is `""` for none |
| `bmlib_version() -> String` | function | The Rust workspace version (`0.3.0`), shared by bmlib and bmlib-ffi; the same for every commit of it, so it cannot identify a build (#493) |
| `JatsArticle` and its parts | records | Owned FFI copies of `bmlib::fulltext::models`, field for field and in bmlib's order. Tables are HTML (`html_content`), as in bmlib |
| `retraction_status(doi, export, crossref, publication_types) -> RetractionVerdict` | function | bmlib#504's rule: `Retracted`, `Concern`, `Clear` or `Unchecked`, the deciding notice, and the lookups that did not answer. `export: nil` means the export was **not consulted**; `[]` means consulted, nothing found |
| `crossref_retraction_requests(dois, mailto) -> CrossrefRequests` | function | Batched Crossref URLs (50 DOIs, at most 4,000 characters) with the DOIs each covers; `unsendable` DOIs (a comma) get `crossref: nil` |
| `parse_crossref_retraction_response(status, body, requested_dois) -> [doi: CrossrefResult]` | function | `Served` (carrying its normalised DOI: `retraction_status` reads it as no answer for any other paper, #508), `NotKnown` or `Unreachable`, keyed by the normalised DOI (doi.org prefix stripped, lower-cased: the same strings as each batch's `dois`). A failed batch makes every DOI in it `Unreachable`. The app owns the transport and the pacing |
| `build_retraction_index(csv, data_as_of) -> RetractionIndexBuild`, `RetractionIndex` | function, object | The Retraction Watch export as one file: `to_bytes()` to store, `RetractionIndex.fromBytes` to reopen, `lookup(doi, pmid)` (`nil` = not consulted), `data_as_of()`, `notice_count()`. Throws `RetractionIndexError.Unusable` — read it as "not consulted". A download truncated on a row boundary still builds: compare `notice_count()` with the index it replaces |
| `ForeignHttpClient`, `BmlibFullTextService` | sketch | **Behind the non-default `http-sketch` feature, so in no binary or binding the apps ship** (#492). The app's transport adapted to bmlib's `HttpClient`, and a probe of the round trip |

The error is `JatsError` in Swift (`JatsError.Xml(reason:)`) and
`JatsException` in Kotlin (`JatsException.Xml`), with three cases:

- **`Xml`**: the document is not well-formed.
- **`NestingTooDeep(depth, limit)`**: the document nests past 256 elements (see below).
- **`Resource`**: the parse thread could not start, so a later call may succeed.

Error fields are named `reason`, never `message`: a `message` field collides
with Kotlin's `Throwable.message` and does not compile.

### Retraction status

The contract is bmlib's `tests/fixtures/retraction_status.json`; the apps'
suites are to assert against the same file. Unlike the JATS records, the retraction
types use **optionals** for absent values: an undated notice is not one dated
`""`, and an export that was not consulted (`nil`) is not one that found
nothing (`[]`). `parse_crossref_retraction_response` takes the HTTP status as
`u16` (`UShort` in Kotlin), which is #496's open question about unsigned
integers.

### The contract the records keep

- **An empty string means the document did not deposit the value.** Nothing
  is invented: no derived title, no numbered label, no formatted citation.
  Only the hrefs (`graphic_url`) are optional. A record's doc comments say what
  each field's empty value means; `has_body`, `citation` and `affiliations` are
  the ones to read before using.
- **Fields are only ever appended.** The Kotlin data classes destructure by
  position and the Swift memberwise initialisers take fields in order. A field
  bmlib adds to its model fails to compile in `src/lib.rs` until it is carried
  across or named as left behind, and `src/tests.rs` holds the values to
  bmlib's own, over a fixture with every field set and over the JATS
  corpus bmlib's reader is held to Python on.

### Nesting depth

The XML reader and bmlib's walk over its tree each recurse once per element.
A stack overflow is not a panic: it kills the app, and no error can report it.
So a document nesting more than **256** elements is refused before parsing.
The deepest article measured nests 43: `PMC12162105`, among the 97,909 in
PMC's `PMC012xxxxxx` baseline package. The parse runs on a thread of its own,
with a stack sized for that depth, so the caller's stack size does not matter.
bmlib's own Rust entry points are not guarded yet (#495).

## Features and profile

- `bmlib` is a dependency **without default features**, so neither the
  bundled SQLite (`sqlite`) nor `ureq`/rustls (`http-ureq`) is compiled. The
  apps own their storage and their transport. This holds for a `-p bmlib-ffi`
  build, which is what every command here runs. A workspace-wide `cargo build`
  also builds the `bmlib` member with its defaults, and feature unification
  then links them in here too.
- `full` turns both back on. Nothing exported needs them; it exists so the
  saving can be measured.
- `bindgen` builds the two generator binaries (`uniffi-bindgen`,
  `uniffi-bindgen-swift`).
- `http-sketch` compiles the HTTP callback sketch (#492). Off by default.
- Ship with `--profile release-ffi` (workspace `Cargo.toml`): fat LTO,
  `opt-level = "s"`, symbols stripped. `panic` stays `unwind`, so UniFFI turns
  a Rust panic in a function returning `Result` into a foreign error instead
  of a crash. Swift calls a non-throwing function (`bmlib_version`) with
  `try!`, so a panic there still traps.

UniFFI is pinned exactly (`=0.32.2`). The generated bindings check the
library's checksums at first call, so the bindings and the binary must come
from the same source revision and the same UniFFI version. The bindings are
generated from the host `--release` build, and the shipped slices are the
`release-ffi` build of the same revision.

The crate needs Rust 1.88 (its own `rust-version`, above the workspace's 1.85):
uniffi's `cargo_metadata` needs 1.86, and the `bindgen` feature's askama needs
1.88.

## Building

Run everything from `rust/`.

### Prerequisites

```bash
rustup target add aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios \
                  aarch64-apple-darwin x86_64-apple-darwin          # Apple
# Apple also needs Xcode's command-line tools (xcodebuild, lipo,
# install_name_tool, strip) and python3, which the script uses.
rustup target add aarch64-linux-android x86_64-linux-android        # Android
cargo install cargo-ndk                                              # Android
export ANDROID_NDK_HOME=~/Library/Android/sdk/ndk/<version>          # Android
```

### Apple: xcframework + Swift bindings

```bash
bmlib-ffi/scripts/build-xcframework.sh <out-dir>            # dynamic frameworks (default)
bmlib-ffi/scripts/build-xcframework.sh <out-dir> --static   # static libraries, for comparison
```

The script builds in a temporary directory and replaces `<out-dir>`'s contents
only once everything has succeeded. Every slice is built through
`bmlib-ffi/scripts/with-remapped-paths.sh`, which remaps the bmlib checkout,
`CARGO_HOME` and the toolchain's sysroot to fixed paths (#490). So no builder's
home directory is compiled into a slice, and two checkouts at different paths
build the same bytes, which a CI rebuild-and-compare guard needs. It reads cargo's target directory from
`cargo metadata`, so `CARGO_TARGET_DIR` is honoured.

Writes `<out-dir>/BmlibFFI.xcframework` (slices `ios-arm64`,
`ios-arm64_x86_64-simulator`, `macos-arm64_x86_64`; minimum iOS 17.0, macOS
14.0) and `<out-dir>/Swift/bmlib_ffi.swift`. A consumer adds the xcframework as
a `binaryTarget` named `BmlibFFI` and compiles the Swift file in a target that
depends on it (bmlibrarian_lite: `Packages/BioMedLit`, target `BmlibCore`).

The macOS slice is universal because an app's macOS archive builds arm64 and
x86_64; an arm64-only slice fails the x86_64 link.

Dynamic frameworks are the default because the committed artifact is an order
of magnitude smaller. The stripped static archives carry every dependency's
objects until the app's linker dead-strips them. The totals are under
"Sizes".

### Android: .so files + Kotlin bindings

```bash
bmlib-ffi/scripts/with-remapped-paths.sh \
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
bmlib checkout (`bmlib.dir` / `BMLIB_DIR`). Run the `.so` build through
`with-remapped-paths.sh`, as above, for the same reason as the Apple slices.

### Bindings from the host build

`uniffi-bindgen` and `uniffi-bindgen-swift` read the UniFFI metadata out of a
built library ("library mode"). Use the unstripped host build:
`target/release/libbmlib_ffi.dylib` on a macOS host, or `libbmlib_ffi.so` on
Linux. The `release-ffi` slices are symbol-stripped.

### Trying it from Python

The same generator writes Python bindings, which is the quickest way to look at
what the reader returns without building an app:

```bash
./target/release/uniffi-bindgen generate --library target/release/libbmlib_ffi.dylib \
    --language python --out-dir /tmp/py && cp target/release/libbmlib_ffi.dylib /tmp/py/
python3 -c 'import sys; sys.path.insert(0, "/tmp/py"); import bmlib_ffi; \
    print(bmlib_ffi.parse_jats(open("article.xml").read()).title)'
```

## Sizes

### The xcframework, at this revision

Measured 2026-10-18 by running `build-xcframework.sh` (dynamic, then
`--static`) on an Apple-silicon Mac, with sizes from `stat -f %z` and
`du -sk`. Re-derive them by running the script again:

| | Dynamic frameworks | Static archives (stripped) |
|---|---|---|
| `ios-arm64` | 634,104 bytes | 10,303,368 bytes |
| `ios-arm64_x86_64-simulator` (two architectures) | 1,245,272 bytes | 20,335,624 bytes |
| `macos-arm64_x86_64` (two architectures) | 1,278,536 bytes | 20,786,680 bytes |
| Whole xcframework (`du -sk`) | 3,192 KiB | 50,312 KiB |

What the dynamic xcframework costs in git is a delta: about 0.8 MB packed per
bmlib bump, because the slices delta-compress (bmlibrarian_lite's record, below).

### Per architecture, and in an app (bmlibrarian_lite's measurement)

These are from bmlibrarian_lite's `doc/cross_platform/bmlib_sync/uniffi_spike.md`
§3 (2026-10-17, `release-ffi`, stripped, at the spike's first revision), and
are not re-derivable from this repository. The Android figures need
bmlibrarian_lite's Gradle build. The static column predates the universal macOS
slice, which is why it has no macOS x86_64 entry and why its total (39 MB) is
below the 49 MiB above.

| Slice | Dynamic library | Static archive (stripped) |
|---|---|---|
| iOS arm64 | 0.67 MB (0.72 MB with `full`) | 10.4 MB (16.2 MB with `full`) |
| iOS simulator arm64 | 0.67 MB | 10.4 MB |
| iOS simulator x86_64 | 0.64 MB | 10.1 MB |
| macOS arm64 | 0.67 MB | 10.4 MB |
| macOS x86_64 | 0.64 MB | — |
| Android arm64-v8a | 0.82 MB (0.89 MB with `full`) | — |
| Android x86_64 | 0.87 MB (0.94 MB with `full`) | — |

bmlibrarian_lite's stub iOS app links the library statically with
`-dead_strip`. There it adds 0.65 MB (0.71 MB with `full`), for the iOS arm64,
simulator and macOS arm64 builds that record measured. So the trim saves only
about 50 KB in the shipped app. What it really saves is the C (SQLite) and
assembly (ring) cross-builds, and 6 MB per static slice.
