# Changelog

All notable changes to the `bmlib` **Rust crate** are documented here. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
crate follows [Semantic Versioning](https://semver.org/).

The Python library is documented separately, in the repository's
[`CHANGELOG.md`](../../CHANGELOG.md).

## [0.2.0] - 2026-09-27

### Changed — breaking

Three public signatures in `fulltext::cache`, for one defect: `default_cache_dir`
read `HOME` on every platform and fell back to `PathBuf::from(".")`, so a process
with no home directory wrote its cache into whatever directory it happened to be
started in. Python reads `HOME` only on POSIX — on Windows `Path.home()` consults
`USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH`, and **never `HOME`** — and where no
home can be determined it raises, which `FullTextService` catches and degrades to
no caching. `None` now travels the whole chain instead of a fabricated directory.

- **`default_cache_dir() -> Option<PathBuf>`,** where it returned `PathBuf`. The
  `Option` is what makes the relocation unrepresentable rather than merely fixed:
  there is no value left for the no-home case to take.
- **`FullTextCache::new(Option<PathBuf>) -> Option<Self>`,** where it returned
  `Self`. `None` returned is Python's `RuntimeError` from `FullTextCache()`; a
  `Some` argument still cannot fail, because this cache creates no directory on
  construction.
- **`impl Default for FullTextCache` is removed.** Python's `FullTextCache()`
  raises where there is no home, and `Default` has no way to report that — an
  infallible default could only panic on such a machine or invent a directory,
  and inventing one is the defect. `FullTextCache::new(None)` is the
  replacement, and the `cache_dir` field is public, so a caller with a directory
  already had one.

### Fixed

- **A machine with no home directory caches nothing instead of writing into the
  current working directory.** `FullTextService::with_default_cache` degrades
  with a warning that names the cause — a caller who cannot determine a home is
  not helped by being told to choose a writable location — where the port used to
  report nothing and silently relocate.
- **A Windows home directory is found the way Windows defines one.**
  `USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH` concatenated (so a rooted
  `HOMEPATH` keeps the drive), and `HOME` is never consulted. Reading `HOME`
  there found a different directory whenever a POSIX-flavoured shell had set it.
- The platform table — macOS `~/Library/Caches`, Windows `~/AppData/Local` with a
  `~/.cache` fallback when that directory does not exist, and `~/.cache`
  elsewhere — is now exercised for all three platforms from one machine, by
  taking the environment lookup and the home directory as arguments rather than
  reading process-global state.

## [0.1.1] - 2026-09-27

Everything below is in the **quality** readers — the rules that read a model's
JSON reply back into a Cochrane assessment or a Tier 2/3 answer. All of them move
toward Python's behaviour, and `src/quality/json_fields.rs` now states the numeric
half of those rules once rather than at each site.

### Fixed

- **An absent `risk_of_bias` no longer fabricates a risk-of-bias table.**
  `parse_cochrane_assessment` filled a missing section with nine `"Unclear risk"`
  domains — a judgement the model never made — and `COCHRANE_RESPONSE_FORMAT`
  explicitly sanctions `null` for a field the text does not report, so a
  *compliant* model reached it. The section is now refused, in Python's wording:
  `the response carries no risk_of_bias section`.
- **A boolean is not a number.** `bool` is an `int` in Python, and a `true` read
  as `1` / `1.0` is the most confident answer there is. Every numeric reader
  refuses it now.
- **A non-finite float is not a measurement.** `"nan"`, `"inf"`, `"-inf"`,
  `"Infinity"` and `"1e400"` were accepted by the Tier 2/3 float reader and by
  `clamped_confidence`; a confidence of `"nan"` was stored as `0.0`, which is a
  claim about the paper rather than a missing value. All are refused, and a
  refused `overall_confidence` is logged at WARNING as Python logs it — that line
  is the only trace the model answered one, an unstated confidence being kept
  under any `min_confidence` bar.
- **A count that is not an integer is no longer truncated into one.** `"45.5"`
  read as `45` and `"nan"` as `0`. `as_int` now follows `int()`: a numeric string
  is parsed (`"120"`, `"  +45  "`, `"1_000"`), a finite float truncates toward
  zero (`100.5` → `100`), and a malformed or non-finite one is unstated. A count
  beyond `i64` is refused rather than saturated at `i64::MAX`, which would record
  a number nobody reported.
- **`clamped_confidence` keeps Python's signed zero.** `(-0.0).clamp(0.0, 1.0)`
  is `-0.0` and a formatter renders it as `-0%`; Python's two-argument `max`
  returns `0.0`.
- **The risk-of-bias refusals use Python's wording**, so a caller matching on the
  message sees the same sentence: `the risk of bias has no '<domain>' domain` and
  `the risk of bias item has no '<key>'`. A domain that is not an object is
  refused rather than read.
- **A section is read leniently, a field is not.** `methods` and
  `support_for_judgement` are annotated as text, so a number or an object for one
  reads as unstated rather than being stringified; `group_sizes` goes through the
  same integer-map rule as its siblings.

## [0.1.0] - 2026-09-27

The first release — the whole port, as [`rust/README.md`](../README.md) lays it
out: `db/`, `citations/`, `context_processor/`, `fulltext/`, `transparency/`,
`publications/`, `quality/`, `llm/`, `agents/` and `templates/`.
