# bmlib.library — A Shared Folder of Research Papers

A library is a folder the user chooses, in which several apps keep the papers
they have fetched, so one download serves them all. It is plain files, with no
database: any app reads it with the same code, a sync service (iCloud,
Dropbox, Syncthing) carries it between machines, and a person can look at it in
a file manager. Rust has the same module as `bmlib::library`, held to this one
by `tests/fixtures/library_cases.json` and the sample library under
`tests/fixtures/library/sample/`; see
[the Rust manual](../../rust/bmlib/docs/manual/library.md).

No extra is needed: the module is standard library only.

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

- **The marker** makes a folder a library. `open_library` writes it into an
  empty folder; it refuses a folder holding anything else.
- **One folder per paper**, named `year author – title (ID)` by `folder_name`
  (at most 150 UTF-8 bytes, valid on macOS, Windows and Linux).
- **Source files are write-once.** The name carries the start of the content's
  SHA-256, so the same bytes are the same file, and a file is never rewritten.
  Kinds: `pdf`, `jats`, `html`, `markdown`, `text` (`SourceKind`).
- **Each installation writes only its own record**, so two apps never write the
  same file and a sync service has nothing to merge. A record names the
  installation, the paper (identifiers, title, year, first author) and the files
  that installation uses (digest, kind, name, origin, size, time saved). An
  *installation* is one app on one machine:
  `Installation("studyanalyzer", "aaaa", "Horst's Mac")` (app `[a-z0-9]{1,40}`,
  id `[a-z0-9-]{1,64}`, a label of 1 to 200 characters a person recognises).
- **A paper is the union of folders whose records share an identifier.** If two
  apps each created a folder before either knew the other's identifier, `find`
  and `group` still see one paper. Every record's identifiers count, so a value
  that lost a conflict inside one folder still finds it.

## Opening, scanning, finding

```python
import tempfile
from pathlib import Path

from bmlib.library import (
    Installation, PaperInfo, SourceKind, canonical_ids, find, open_library,
)

root = Path(tempfile.mkdtemp())

# An empty folder becomes a library; an existing one is checked.
lib = open_library(root)

me = Installation("studyanalyzer", "aaaa", "My Mac")
paper = PaperInfo(
    ids=canonical_ids(pmid="0038012345", doi="10.1/X"),
    title="Metformin and cognition",
    year=2024,
    first_author="Smith",
)

# Save a file. It lands in the paper's folder and in my record.
file = lib.save(me, paper, b"%PDF a paper", SourceKind.PDF, "https://example.org/x.pdf")
assert file.name.startswith("pdf-") and file.name.endswith(".pdf")

# Saving the same bytes again is the same file; nothing is written twice.
assert lib.save(me, paper, b"%PDF a paper", SourceKind.PDF, "again") == file

# Another app finds the paper by any identifier it has, and reads the file.
scan = lib.scan()
folders = find(scan, canonical_ids(doi="10.1/x"))
assert len(folders) == 1
assert len(folders[0].used_by(file.sha256)) == 1
assert lib.read(file) == b"%PDF a paper"

# Stop using the file. It stays on disk for the others.
lib.release(me, file)
assert lib.read(file) == b"%PDF a paper"

# The manual clear: delete the file, or the whole paper.
lib.remove_folder(file.folder)
```

The calls are those of the Rust manual's table, with Python's spelling:
`open_library(root)`, `lib.root`, `lib.scan()`, `find(scan, ids)`,
`find_digest(scan, sha256)`, `group(scan)`, `lib.read(file)`,
`lib.save(who, paper, data, kind, origin)`, `lib.record_use(who, paper, file)`
(note a file another app saved in my record), `lib.release(who, file)`,
`lib.remove_file(file)`, `lib.remove_folder(name)`,
`canonical_ids(pmid=, doi=, pmcid=)`, `folder_name(info)`,
`folder_name_numbered(info, n)`, `sha256_hex(data)`.

A `Scan` holds `folders: list[PaperFolder]` (each with `folder`, `records`,
`files_on_disk`, `ids()` and `used_by(sha256)`) and `problems: list[Problem]`.
A `FileRef` is `(folder, name, sha256, kind)`.

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
  scan reports as `"unrecorded_file"`; saving the same bytes again records
  it.
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

| `Problem.kind` | Meaning |
|---|---|
| `"unreadable_record"` | A record file is not valid; it is not used |
| `"unreadable_folder"` | A paper folder, or its `records/`, cannot be listed |
| `"newer_record"` | A record written by a newer layout version; update the app |
| `"record_not_downloaded"` | A record present only as an iCloud placeholder; its installation's uses are unknown until it is downloaded |
| `"misnamed_record"` | A valid record whose file name is not its installation's (a "copy of" by a sync conflict) |
| `"conflicting_ids"` | Two records of one folder hold different values for one identifier (`field`); both count for `find` and `group` |
| `"unrecorded_file"` | A source file no record lists (a crash, or a record lost) |
| `"leftover_temp"` | A temporary of an atomic write, in flight or left by a crash; never deleted, and never a user's file |
| `"stray_entry"` | Something in `papers/` that is not a folder |

## Errors and what to tell the user

Every failure is an exception, a subclass of `LibraryError`, with the fields
named in the Rust page:

| Exception | What happened | What to tell the user |
|---|---|---|
| `Unreachable` | The folder is missing or not a folder | "The library folder cannot be reached" (an unplugged drive, a revoked grant); offer to choose again |
| `NotALibrary` | The folder holds other files and no marker | "This folder is not a paper library"; never offer to use it |
| `NewerLibrary` | The marker's version is newer than 1 | "Update this app to use this library" |
| `ReadOnly` | The library cannot be written | "The library is read-only"; reading still works |
| `BadIdentifier` | A PMID, DOI or PMCID is not valid | Name the field and the value |
| `BadInstallation` | An app, id or label is not acceptable | A programming error, or an id from outside |
| `BadName` | A folder or file name given to the library is not one plain path component (or a `FileRef` name that is not the library's name for those bytes) | A programming error, or a name from outside; nothing was touched |
| `Missing` | The named file or folder is not there | "That file was removed"; offer to fetch it again |
| `NotDownloaded` | Only an iCloud placeholder is present | "Download the file first" (open it in Finder) |
| `DigestMismatch` | The bytes differ from the recorded digest | "The file is damaged or changed"; never use its contents |
| `FileClash` | A file of that name holds other bytes | A damaged file; nothing was written |
| `NewerRecord` | A record, or every record of the folder the paper belongs in, is from a newer layout | "Update this app" |
| `Io` | Another file-system failure | Show `reason` and the path |

`str(error)` is a sentence an app can show as it is.

## FullTextService on a library

`FullTextService` can read papers from a shared library before its cache and
keep what it downloads there. A library is optional: without one the service
behaves exactly as before.

### Building a service with a library

```python
from bmlib.fulltext import FullTextService
from bmlib.library import Installation, open_library

library = open_library("/Users/me/Papers")          # the shared folder
me = Installation("myapp", "aaaa", "My Mac")        # this installation of the app

service = FullTextService(library=library, installation=me)
result = service.fetch_fulltext(pmc_id="PMC7614751", doi="10.1/x")

print(result.source)           # "library" when the library held the JATS
print(result.library_files)    # the library files used or saved
print(result.library_failure)  # None, or why the library could not be used
```

`library` and `installation` are given together or not at all (`ValueError`
otherwise).

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
owed* mark (see [the cache](fulltext.md#fulltextcache)) and the abstract kept beside a PDF,
both keyed by `identifier`. When the library fails, or the request has no valid
identifier, the cache keeps everything, as without a library.

## Rust names

| Python | Rust |
|---|---|
| `open_library(root)` | `Library::open(root)` |
| exception `X` | `LibraryError::X { .. }` |
| `canonical_ids(..)` | `PaperIds::canonical(..)` |
| `Problem(kind="unreadable_record", ..)` | `Problem::UnreadableRecord { .. }` |
| `SourceKind.PDF` | `SourceKind::Pdf` |
| `lib.save(.., data, ..)` | `lib.save(.., bytes, ..)` |
