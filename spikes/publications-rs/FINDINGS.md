# Findings: porting a slice of `publications/storage.py`

**The question.** `spikes/db-rs` established that `db/` ports well, but `db/` is
787 of 25,881 lines and the module with the least ecosystem risk. Two things it
could not answer: does `Value`/`Row` hold up once real code passes through it,
and do the `DECISIONS.md` invariants survive a port?

**The answer.** Both, better than expected. The store path came out at
parity with Python on line count, the model layer came out **77% smaller**, and
every invariant the slice carries is pinned by a test that a mutation kills.

---

## 1. The invariants port, and the tests still bite

22 tests ported from `test_backends.py::TestStorage` and
`::TestGrantAndAffiliationStorage`, passing on the first run. That is weak
evidence on its own — a test suite that cannot fail passes too — so six
mutations were applied, each reproducing an actual defect the register records:

| Mutation | Killed by |
|---|---|
| `replace_child_rows` scoped by publication, not source | 3 tests |
| `relocate_child_rows` moves every source (keep row stops winning) | 2 tests |
| merge overwrites instead of `COALESCE` | 1 test |
| open access stops being a one-way latch | 1 test |
| DOI no longer case-folded | 1 test |
| unnamed-source guard removed | 1 test |

The first is the one that matters most: scoping by publication alone is the
real defect that made PubMed's grants replace OpenAlex's and then OpenAlex's
replace PubMed's, so the stored answer depended on whichever source synced
last, silently. It stays caught.

## 2. The line ratio inverts against `db/`

| | Python | Rust |
|---|---|---|
| `storage.py` → `storage.rs` + `identifiers.rs` | 496 | 488 |
| `Publication` + `Grant` + `AuthorAffiliation` | 169 | **39** |
| — its own error type | — | 33 |

Code lines, blanks and comments excluded. The store path is **parity**, against
`db/`'s 1.23×.

That is the more useful number of the two spikes. `db/`'s overhead was
`Value`/`Row`/`DbError` — 168 lines replacing what duck typing and exceptions
gave free. Those are written **once for the whole port**, not per module.
`storage.py` is SQL and control flow, which is the same in both languages, so
it pays nothing further.

## 3. `serde` deletes the model layer

169 Python code lines for the three models — including six hand-written
`to_dict`/`from_dict` methods — become 39 Rust lines and two derives.

This revises something I said earlier in this session. I called `Value`/`Row`
"the thing to get right early, because it constrains everything downstream."
Half right: `Value` is still the boundary type and still worth deliberate
design. But the *model* layer, which is 72 dataclasses and 54 `to_dict` /
`from_dict` pairs across bmlib, gets dramatically cheaper — and the
serialization the sidecar plan needs at its boundary falls out of the same
derive rather than being extra work.

## 4. Two invariants change shape

- **`Grant.source` is a `String`, not `Option<String>`.** The Python guard has
  to reject both `None` and `""`; in Rust `None` is unrepresentable, so only
  the empty string survives. The guard is still needed and still tested —
  `""` is exactly the dataclass default a forgetful caller produces, and the
  `NOT NULL` column accepted it happily.
- **`_optional_column` survives, simplified.** A column added after a release
  may be absent from a database whose owner has upgraded the library but not
  re-run the schema. Python catches `IndexError` on `sqlite3.Row` and
  `KeyError` on a dict; here it is `row.get(name).ok()`, one path for both.

## 5. What the slice changed upstream

`bmlib_db::transaction`'s closure was hard-wired to `DbError`. A module with
its own error type — which any module ported above `db/` will have — cannot use
it at all: every `?` in the store path failed to compile with *"`?` couldn't
convert the error to `DbError`"*.

Fixed by adding `transaction_with<T, E: From<DbError>, F>` beside it.

**The first attempt was wrong and is worth recording.** Making `transaction`
*itself* generic over `E` compiles, but `?` only *constrains* `E` and never
pins it, so inference fails at almost every call site — including inside a
typed function. Measured on `db-rs`: it needed an `Ok::<_, DbError>`
annotation in the migration runner and at five call sites in the tests. Two
entry points cost one name; one generic entry point cost an annotation nearly
everywhere.

## 6. The batching property holds at realistic scale

`spikes/db-rs` proved a toy helper composes standalone and inside a batch
block. Here it is the real store path: 200 `store_publication` calls — each
opening its own transaction, each writing a publication and a grant — inside
one caller block paying a single commit. And a day that fails after 50 records
discards all 50.

That is `publications.sync()`'s one-commit-per-day shape, working.

## 7. Small frictions, named so they are not rediscovered

- **`abstract` is a Rust keyword.** The field is `r#abstract`. It is one of
  bmlib's most-used field names; every port meets it on day one.
- **`..Default::default()` is a decent `**kwargs`.** Python's `_pub(**kwargs)`
  test helper — build with defaults, override two fields — ports to struct
  update syntax closely enough that the tests read almost the same.
- **`BTreeMap` over `HashMap`** where insertion order reaches the database, so
  a test asserting on stored order does not depend on hashing.

## What this spike did not establish

- **SQLite only.** The PostgreSQL arm of `insert_publication` (`RETURNING id`,
  the one irreducibly dialect-specific need) is written and **never run**. The
  PostgreSQL DDL is not reproduced at all.
- **No `sync.py`.** The day-level rules — #88–#90's reconciliation, #95's
  12:00 UTC boundary, #96/#105's retrievable cap — are the densest invariants
  in the codebase and are entirely untouched. This slice is the *storage* half.
- **22 tests, not the ~60 in `test_backends.py`.** Retraction storage and the
  schema-upgrade tests are not ported.
- **No fetchers, no HTTP, no performance measurement.**
- **`json` columns go through `serde_json` here, `json` there.** Byte-identical
  output was not checked, and a differential test against the Python
  implementation — which is what the migration plan actually calls for — was
  not run.

## What it means for the migration plan

The `db/`-first ordering still holds, and the case for it is stronger: its
overhead is a fixed cost the rest of the port does not repeat.

The revision is to the estimate. I sized the full port at "multi-person-month
to person-year, and Rust will not be meaningfully shorter than 26k lines". On
this evidence the second half of that is wrong for the module layer — parity on
logic, and a large reduction wherever hand-written serialization exists. The
`db/` ratio was the worst case, not the average.

What has *not* been de-risked is anything above storage: `sync.py`'s day rules,
the fetchers, and — still the real blocker — `fulltext/`'s PDF half.
