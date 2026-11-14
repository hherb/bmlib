# bmlib::library — A Shared Folder of Research Papers

A library is a folder the user chooses, in which several apps keep the papers
they have fetched, so one download serves them all. It is plain files, with no
database: any app reads it with the same code, a sync service (iCloud,
Dropbox, Syncthing) carries it between machines, and a person can look at it in
a file manager. Python has the same module as `bmlib.library`, held to this one
by `tests/fixtures/library_cases.json` and the sample library under
`tests/fixtures/library/sample/`; see
[the Python manual](../../../../docs/manual/library.md).

| Need | Feature |
|---|---|
| Everything in `bmlib::library` | none (it builds with `default-features = false`) |

## What a library looks like

```text
My Papers/
├── bmlib-library.json                      the marker: {"format": "bmlib-library", "version": 1}
└── papers/
    └── 2024 Smith – Metformin and cognition (PMID 38012345)/
        ├── pdf-9b101e6bb96e.pdf            a source file: {kind}-{first 12 hex of sha256}.{ext}
        ├── jats-354be1cb35ec.xml
        └── records/
            ├── studyanalyzer-aaaa.json     one record per installation: {app}-{id}.json
            └── medicalfactchecker-bbbb.json
```

- **The marker** makes a folder a library. `Library::open` writes it into an
  empty folder; it refuses a folder holding anything else.
- **One folder per paper**, named `year author – title (ID)` by `folder_name`
  (at most 150 UTF-8 bytes, valid on macOS, Windows and Linux).
- **Source files are write-once.** The name carries the start of the content's
  SHA-256, so the same bytes of one kind are the same file, and a file is never
  rewritten. Kinds: `pdf`, `jats`, `html`, `markdown`, `text` (`SourceKind`).
- **Each installation writes only its own record**, so two apps never write the
  same file and a sync service has nothing to merge. A record names the
  installation, the paper (identifiers, title, year, first author) and the files
  that installation uses (digest, kind, name, origin, size, time saved). An
  *installation* is one app on one machine:
  `Installation::new("studyanalyzer", "aaaa", "Horst's Mac")` (app
  `[a-z0-9]{1,40}`, id `[a-z0-9-]{1,64}`, a label of 1 to 200 characters a
  person recognises).
- **A paper is the union of folders whose records share an identifier.** If two
  apps each created a folder before either knew the other's identifier, `find`
  and `group` still see one paper. Every record's identifiers count, so a value
  that lost a conflict inside one folder still finds it.

## Opening, scanning, finding

```rust
use bmlib::library::{
    find, PaperIds, Installation, Library, PaperInfo, SourceKind,
};

let root = std::env::temp_dir().join(format!("bmlib-manual-library-{}", std::process::id()));
std::fs::create_dir_all(&root)?;

// An empty folder becomes a library; an existing one is checked.
let lib = Library::open(&root)?;

let me = Installation::new("studyanalyzer", "aaaa", "My Mac")?;
let paper = PaperInfo {
    ids: PaperIds::canonical(Some("0038012345"), Some("10.1/X"), None)?,
    title: Some("Metformin and cognition".into()),
    year: Some(2024),
    first_author: Some("Smith".into()),
};

// Save a file. It lands in the paper's folder and in my record.
let file = lib.save(&me, &paper, b"%PDF a paper", SourceKind::Pdf, "https://example.org/x.pdf")?;
assert!(file.name.starts_with("pdf-") && file.name.ends_with(".pdf"));

// Saving the same bytes again is the same file; nothing is written twice.
assert_eq!(lib.save(&me, &paper, b"%PDF a paper", SourceKind::Pdf, "again")?, file);

// Another app finds the paper by any identifier it has, and reads the file.
let scan = lib.scan()?;
let ids = PaperIds::canonical(None, Some("10.1/x"), None)?;
let folders = find(&scan, &ids);
assert_eq!(folders.len(), 1);
assert_eq!(folders[0].used_by(&file.sha256).len(), 1);
assert_eq!(lib.read(&file)?, b"%PDF a paper");

// Stop using the file. It stays on disk for the others.
lib.release(&me, &file)?;
assert!(lib.read(&file).is_ok());

// The manual clear: delete the file, or the whole paper.
lib.remove_folder(&file.folder)?;
# std::fs::remove_dir_all(&root).ok();
# Ok::<(), Box<dyn std::error::Error>>(())
```

| Call | What it does |
|---|---|
| `Library::open(root)` | Opens the library at `root`; writes the marker and `papers/` into an empty folder |
| `lib.root()` | The folder |
| `lib.scan()` | Reads every paper folder into a `Scan`; never writes |
| `find(&scan, &ids)` | Every folder of the paper(s) whose identifiers share one with `ids` (the whole linked group) |
| `find_digest(&scan, sha256, kind)` | A `FileRef` for a file named for that digest, whichever folder holds it; with `Some(kind)`, only the file stored as that kind |
| `group(&scan)` | Indices of folders connected by shared identifiers |
| `lib.read(&file)` | The file's bytes, checked against `file.sha256` |
| `lib.save(&who, &paper, bytes, kind, origin)` | Writes the file once, then records it |
| `lib.record_use(&who, &paper, &file)` | Notes in `who`'s record a file already in the library (one another app saved): reads it, digest-checked, then merges as `save` does |
| `lib.release(&who, &file)` | Drops the file from `who`'s record only |
| `lib.remove_file(&file)` / `lib.remove_folder(name)` | Delete a file or a paper's folder (the manual clear) |
| `PaperIds::canonical(pmid, doi, pmcid)` | Canonical identifiers from raw ones: leading zeros dropped, the DOI lower-cased; a blank value is `None` |
| `folder_name(&info)` / `folder_name_numbered(&info, n)` | The folder name, and the ` (n)` form when another paper has it |
| `sha256_hex(bytes)` | The lower-case hex digest used throughout |

A `Scan` holds `folders: Vec<PaperFolder>` (each with `folder`, `records`,
`files_on_disk`, and `ids()` / `used_by(sha256)`) and `problems: Vec<Problem>`.
A `FileRef` is `{ folder, name, sha256, kind }`: take it from `save` or build it
from a record's `FileEntry`.

## What the rules mean in practice

- **Nothing is overwritten.** If a file of that name already holds other bytes,
  `save` returns `FileClash`. The name has 48 bits of the digest in it, so this
  means a damaged file, not a collision.
- **Nothing is deleted automatically.** The library never evicts, expires or
  cleans. `release` forgets one installation's use of a file and leaves the
  file; only `remove_file` and `remove_folder`, which an app calls when the user
  asks, delete anything. Other installations' records for a removed file then
  read `Missing`, which the app can show as "this file was removed".
- **A crash between the file and the record is harmless.** `save` writes the
  file first and the record second. A crash leaves an unrecorded file, which a
  scan reports as `Problem::UnrecordedFile`; saving the same bytes again records
  it.
- **When `save` returns, the file survives a crash.** Each file, record and
  folder it makes, or finds already there, has its directory synced after it
  appears (`F_FULLFSYNC` on macOS), so an app may drop its own copy once `save`
  returns (#768). Not yet on Windows, where a directory cannot be synced this
  way.
- **The same bytes saved as two kinds are two files** (`text-….txt` and
  `markdown-….md`), each its own entry in a record, and `release` drops only
  the kind it is given (#767). Pass the kind to `find_digest` to find the file
  stored as that kind; without it, the first kind present wins.
  `used_by(sha256)` is about the bytes, so it lists an installation using
  either kind.
- **Choosing the folder.** `save` uses the first folder holding a record that
  shares an identifier with the paper, except that bytes are kept once: if the
  library already holds these bytes, the save goes into that folder even when
  the identifiers find another. A paper found by neither goes to a new folder.
  A new folder takes `folder_name`; if another paper already has that name,
  compared without regard to case, it takes ` (2)`, ` (3)` and so on, fitted so
  the whole name stays within 150 bytes. A same-named folder that is the same
  paper, or has no readable record yet (another app's claim in progress), is
  reused. A folder whose records are all from a newer layout is not reused and
  not skipped: `save` fails with `NewerRecord` rather than make a second,
  silent copy.
- **Names are checked.** A Windows device name (`con`, `nul`, `com1` …) as a
  folder name gets a `_` in front, after the length is fitted. A folder or file
  name that is not one plain path component is `BadName`.
- **iCloud placeholders.** A file shown as `.name.icloud` is online-only.
  `read` reports it as `NotDownloaded` rather than `Missing`, and `open` counts
  it as a user's file, so a library is never started in a folder of someone's
  papers. When an installation's own record is a placeholder, `save` and
  `release` fail with `NotDownloaded` and write nothing: a fresh record would
  replace the real one when it came back, losing its entries.
- **Evicted (dataless) files.** macOS 14 and later, and iOS, evict a file
  under its real name instead (iCloud Drive, and Dropbox or OneDrive through
  File Provider). Every read here runs with the thread's materialisation of
  such files turned off, so it fails at once rather than downloading: the
  file reads as `NotDownloaded`, and an evicted record as a placeholder record
  does. A read never waits for the network (#769).
- **Only real files and folders are used** (#761). A record, a source file or
  the marker that is a symbolic link, a FIFO or a device is never read. A scan
  reports such a record as `Problem::UnreadableRecord` with the reason
  `not a regular file`; `read`, `record_use`, `save` and `release` fail with
  `LibraryError::Io` and that reason, and `open` does for the marker. A paper
  folder that is a link is a `Problem::StrayEntry` in a scan, and `read`,
  `record_use`, `release` and `remove_file` refuse it with `LibraryError::Io`
  (`not a folder`), so nothing is read, written or deleted outside the library
  through it; `save` treats its name as taken. A `records/` that is a link is
  `Problem::UnreadableFolder`, and `save` refuses it with `LibraryError::Io`
  (`not a folder`). A `papers/` that is a link is refused the same way by
  `open`, `scan`, `save`, `remove_folder` and every call that addresses a file,
  even when it was swapped in after the library was opened. An entry that
  cannot be examined (a folder that cannot be searched, an I/O error) is never
  taken for absent: a scan reports it as `Problem::UnreadableFolder`, and the
  other calls fail with `LibraryError::Io` or `LibraryError::ReadOnly`. A save
  does not yet act on those reports, so a paper whose folder is unreadable can
  get a second folder (#778). The library's own root may be a link.
- **Concurrency.** Updates to a record are serialised within one process, so
  threads of one installation do not lose each other's entries. Two *processes*
  of one installation are out of scope. Different installations never share a
  file.
- **Numbers.** A record's `size` and `year` read `-0` as 0.
- **Cost.** `save` scans the whole library on each call, so it finds the
  paper's folder wherever another app put it. Measured in Python on a laptop
  SSD: about 0.12 s per save at 1,000 papers and 0.5 s at 4,000, nearly all of
  it the scan (`group` is linear). An app saving many files at once pays it per
  file.

## Problems a scan reports

A scan never fails on one bad folder. Everything odd is a `Problem` and is
never silently dropped; show them (a library moves between apps and
versions, so some will be real).

| `Problem` | Meaning |
|---|---|
| `UnreadableRecord` | A record file is not valid; it is not used |
| `UnreadableFolder` | A paper folder, or its `records/`, cannot be listed or examined, or `records/` is not a folder (a symbolic link included) |
| `NewerRecord` | A record written by a newer layout version; update the app |
| `RecordNotDownloaded` | A record present only as an iCloud placeholder or evicted (dataless); its installation's uses are unknown until it is downloaded |
| `MisnamedRecord` | A valid record whose file name is not its installation's (a "copy of" by a sync conflict) |
| `ConflictingIds` | Two records of one folder hold different values for one identifier (`field`); both count for `find` and `group` |
| `UnrecordedFile` | A source file no record lists (a crash, or a record lost) |
| `LeftoverTemp` | A temporary of an atomic write, in flight or left by a crash; never deleted, and never a user's file |
| `StrayEntry` | Something in `papers/` that is not a folder, a symbolic link to one included |

## Errors and what to tell the user

Every fallible call returns `Result<_, LibraryError>`. The variants:

| Variant | What happened | What to tell the user |
|---|---|---|
| `Unreachable` | The folder is missing or not a folder | "The library folder cannot be reached" (an unplugged drive, a revoked grant); offer to choose again |
| `NotALibrary` | The folder holds other files and no marker | "This folder is not a paper library"; never offer to use it |
| `NewerLibrary` | The marker's version is newer than 1 | "Update this app to use this library" |
| `ReadOnly` | The library cannot be written | "The library is read-only"; reading still works |
| `BadIdentifier` | A PMID, DOI or PMCID is not valid | Name the field and the value |
| `BadInstallation` | An app, id or label is not acceptable | A programming error, or an id from outside |
| `BadName` | A folder or file name given to the library is not one plain path component (or a `FileRef` name that is not the library's name for those bytes) | A programming error, or a name from outside; nothing was touched |
| `Missing` | The named file or folder is not there | "That file was removed"; offer to fetch it again |
| `NotDownloaded` | Only an iCloud placeholder is present, or the file is evicted (dataless) | "Download the file first" (open it in Finder) |
| `DigestMismatch` | The bytes differ from the recorded digest | "The file is damaged or changed"; never use its contents |
| `FileClash` | A file of that name holds other bytes | A damaged file; nothing was written |
| `NewerRecord` | A record, or every record of the folder the paper belongs in, is from a newer layout | "Update this app" |
| `Io` | Another file-system failure | Show `reason` and the path |

`Display` for each variant is a sentence an app can show as it is.

## FullTextService on a library

`FullTextService` can read papers from a shared library before its cache and
keep what it downloads there. A library is optional: without one the service
behaves exactly as before.

### Building a service with a library

```rust
use std::sync::{Arc, Mutex};
use bmlib::fulltext::{FullTextRequest, FullTextService};
use bmlib::library::{Installation, Library, PaperIds, PaperInfo, SourceKind};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};

const JATS: &str = "<article><front><article-meta>\
    <article-id pub-id-type=\"pmc\">PMC123</article-id>\
    <title-group><article-title>A trial</article-title></title-group>\
    </article-meta></front><body><sec><title>Results</title>\
    <p>Mortality fell.</p></sec></body></article>";

// A client that counts its requests and finds nothing.
#[derive(Default)]
struct Offline(Mutex<usize>);

impl HttpClient for Offline {
    fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
        *self.0.lock().unwrap() += 1;
        Ok(HttpResponse::from_bytes(404, Vec::new()))
    }
}

let root = std::env::temp_dir().join(format!("bmlib-manual-ftlib-{}", std::process::id()));
std::fs::create_dir_all(&root)?;
let library = Library::open(&root)?;
let me = Installation::new("myapp", "aaaa", "My Mac")?;

// Another app (or an earlier lookup) kept this paper's JATS.
let paper = PaperInfo {
    ids: PaperIds::canonical(None, None, Some("PMC123"))?,
    title: Some("A trial".into()),
    year: None,
    first_author: None,
};
library.save(&me, &paper, JATS.as_bytes(), SourceKind::Jats, "https://example.org/x.xml")?;

let client = Arc::new(Offline::default());
let service = FullTextService::new(client.clone(), "you@example.org")
    .with_library(library, me);

let request = FullTextRequest { pmc_id: Some("PMC123".into()), ..FullTextRequest::default() };
let result = service.fetch_fulltext(&request)?;

assert_eq!(result.source, "library");
assert!(result.html.as_deref().unwrap().contains("Mortality fell."));
assert_eq!(result.library_files.len(), 1);
assert_eq!(result.library_failure, None);
assert_eq!(*client.0.lock().unwrap(), 0); // the library ended the lookup: no network
# std::fs::remove_dir_all(&root).ok();
# Ok::<(), Box<dyn std::error::Error>>(())
```

`FullTextService::with_library(library, who)` takes the `Library` and the
`Installation` together; `result.library_files` is a `Vec<FileRef>` and
`result.library_failure` an `Option<String>`.

### The lookup order

The library is keyed by the request's `pmid`, `doi` and `pmc_id`, not by
`identifier`.

1. **The library's JATS ends the lookup, with no network.** It is used when it
   parses, has a body, and none of its own identifiers contradicts the
   request's (an identifier missing on either side agrees). The result's
   `source` is `"library"`, its HTML is rendered from the JATS on every
   lookup, and `jats_xml` holds the file's bytes. The library's PDF, if it
   holds one, is the result's `file_path`, beside the JATS, even when
   `fetch_pdf_with_jats` is off. When the library holds no PDF and
   `fetch_pdf_with_jats` is on, the PDF is sought once, as a first download
   would seek it, in the lookup that first notes the JATS in this
   installation's record; a PDF found is kept in the library. Later lookups
   seek again only while the cache's *PDF still owed* mark says a failure may
   pass. This needs a cache and an `identifier`, as the mark does; without
   them, or when the note fails, nothing is sought.
2. **A library that holds only a PDF still runs the JATS tiers.** A PDF in the
   cache ended a lookup; a library PDF does not, because the service prefers
   JATS text. A paper held only as a PDF therefore costs the JATS tiers'
   requests on every lookup, until a JATS is kept in the library.
3. **A held PDF replaces the PDF tiers.** When the lookup ends on JATS from the
   chain, or on no JATS at all, the library's PDF is used in place of a PDF
   download, and in place of the caller's own `pdf` entries
   (`fulltext_sources`), which are then not fetched. If noting its use fails
   (a read-only library), the PDF is still used and the failure is set.
4. **What the chain downloads is kept in the library**, a JATS and a PDF each
   saved once, not in the cache. A PDF is then downloaded even when the
   request has no `identifier`.

Each library file a lookup uses or saves is noted in the installation's
record. The library's HTML, Markdown and text files are not used by the
service; an app finds them through the library API itself.

Of a paper's files of one kind, the newest is used: each file ranks by the
earliest time a record lists it, since noting a use stamps the time of the
note. A file is used only if at least one record listing it names the paper
without contradicting the request's identifiers (an identifier missing on
either side agrees). Two papers whose files share bytes (a publisher's stub
PDF, say) share a folder, because bytes are kept once; this rule stops one
paper being given a file that only the other paper's records list.

**Cost.** A lookup scans the library once. Each file saved costs one more scan
(by `save`), and noting a file not yet in this installation's record reads that
paper's folder again; using a held PDF later in the chain costs one more scan.

### `library_files` and `library_failure`

`FullTextResult.library_files` lists the library files the lookup used or
saved, in order (empty when nothing came from the library).

`FullTextResult.library_failure` is `None` when the library worked or none is
set. Otherwise it is a sentence naming the paper, the file and the problem: the
library could not be read or written, a file failed its digest check, or this
installation's use could not be noted. A library error never stops a lookup and
is never raised from it. It is logged once at WARNING, the rest of the lookup
runs as without a library, and the result carries the flag.

**What an app should do with a failure:** keep what the lookup returned as
*waiting for the library*: hold on to it outside the library, and try to put it
there again later. The cache is a staging area in that case, not a second
store: the app copies the bytes out, as StudyAnalyzer does today.

A library JATS that has no body or does not parse is not a failure of the
library: it is warned of, not served, and the lookup goes on.

### A folder whose JATS is another paper's

A record is a claim, not proof. When the folder found for the request holds a
JATS whose own identifiers contradict the request's, the lookup does not serve
it and does not trust that folder's PDF. It keeps **nothing** in the library
(the request's identifiers would file the new bytes in that same folder), runs
the chain with the cache as without a library, and sets `library_failure`
naming the folder. The app keeps the source as waiting and can show the user
the folder to put right.

### Identifiers

The request's `pmid`, `doi` and `pmc_id` are made canonical one field at a
time. A value the library refuses is dropped with a WARNING naming the field,
and the others are used. A request with no valid identifier does not use the
library: it runs as without one and sets no `library_failure`, since that is
not a failure.

### What stays in the cache

With a library, the cache keeps only what the library does not: the *PDF still
owed* mark (see [the cache](fulltext.md#the-cache)) and the abstract kept beside a PDF,
both keyed by `identifier`. When the library fails, or the request has no valid
identifier, the cache keeps everything, as without a library.

## Python names

| Rust | Python |
|---|---|
| `Library::open(root)` | `open_library(root)` |
| `LibraryError::X { .. }` | the exception class `X`, all subclasses of `LibraryError` (`Unreachable`, `NotALibrary`, `NewerLibrary`, `ReadOnly`, `BadIdentifier`, `BadInstallation`, `BadName`, `Missing`, `NotDownloaded`, `DigestMismatch`, `FileClash`, `NewerRecord`, `Io`) |
| `PaperIds::canonical(..)` | `canonical_ids(..)` |
| `Problem::UnreadableRecord { .. }` | `Problem(kind="unreadable_record", ..)` (one dataclass; fields a kind does not use are `None`) |
| `SourceKind::Pdf` | `SourceKind.PDF` (a `str` enum with the same values) |
| `lib.save(.., bytes, ..)` | `lib.save(.., data, ..)` |
| `find`, `find_digest`, `group`, `folder_name`, `folder_name_numbered`, `sha256_hex`, `parse_record`, `render_record` | the same names |
