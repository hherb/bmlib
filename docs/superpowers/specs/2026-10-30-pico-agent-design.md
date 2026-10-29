# The PICO agent, with verified quotes

_StudyAnalyzer's item 7 (its design §6.4, §7). Python and Rust, same PR, held
to each other by an oracle. Written 2026-10-30 with the maintainer._

## Why

StudyAnalyzer's first analysis tab is a PICO breakdown of the open paper:
population, intervention, comparison, outcome, plus study type and sample
size. The paper sits on the right and the analysis on the left, and every part
links to the passages it rests on. Clicking a citation highlights that passage
in the paper. Not every paper has a PICO: reviews, case reports and editorials
do not. The tab must say so plainly instead of inventing one.

bmlibrarian has a PICO agent (`src/bmlibrarian/agents/pico_agent.py`, 27
tests). It has a suitability check and an extraction with per-part guidance and
a confidence rubric. Its parts carry no quotes, so nothing it says can be
checked against the paper, and that is what StudyAnalyzer exists to fix. The
maintainer's rule is that shared logic lives in bmlib, so the agent and its
quote check are ported into bmlib for every app, not written into
StudyAnalyzer.

## Goals

1. **A suitability check**: is this paper an intervention study with a
   PICO? It reports `is_suitable`, `is_intervention_study`, `has_comparison`,
   `study_type`, `confidence` and `rationale`, as upstream does.
2. **An extraction**: the four parts, study type and sample size. Each part
   carries the model's description, a confidence, and **citations verified
   against the paper**.
3. **Both languages**: Python `bmlib.quality` and Rust `bmlib::quality`,
   giving the same result for the same model replies, held together by a
   differential oracle (the Cochrane assessor's precedent, below).

## What a part reports (the maintainer's decisions)

For every part the model returns a description and the quotes it copied from
the paper. bmlib locates each quote with `find_passage` (`bmlib.citations`) in
the text the caller passed. **A citation is the paper's own span** (`start`,
`end`, the paper's wording, similarity), never the model's copy. Each part then
falls into one of three states:

- **Verified**: at least one quote was found. The found quotes are the
  part's citations. Quotes that were not found are kept as the model wrote
  them and counted, so the app can say *"1 quote could not be found in the
  paper"*.
- **Unverified**: the model described the part but none of its quotes was
  found, or it gave none. **The description is still returned and shown**,
  under a label such as *"Unsure – model could not reproduce Population from
  the text provided"*, so the reader can decide. bmlib returns the state; the
  app writes the wording.
- **Not reported**: the model says the paper does not state this part (its
  *"Not clearly stated"* case). That is an honest absence, not a failure, and
  it needs no quote. The model says so in a boolean field, not by bmlib
  matching the phrase.

**A single-arm study's comparison is reported, not absent.** "There was no
comparison group" is something the paper states, and a quote can back it. So
the prompt asks for it as a reported part with its quote. *Not reported* is
kept for a paper that is silent on the point. bmlib does not check this
against the suitability check's `has_comparison`, because the two calls are
independent. *My call.*

**Nothing is discarded for low confidence.** Upstream drops any extraction
whose overall confidence is under 0.5. Here every confidence is returned and
the reader judges (the maintainer agreed). bmlib computes the overall
confidence rather than asking the model for it: it is the mean of the
confidences of the parts that are **reported** and **carry a number**. A
correctly identified absence does not pull the mean down. When no such part
exists, the overall confidence is unknown. *My call.*

**Quotes are searched in `text` only, never in `title`.** A caller who wants a
title quote to count includes the title in `text`. `CanonicalText.text`
already starts with the title. *My call.*

**The two calls are independent.** The app runs the suitability check first
and shows a "not suitable" answer plainly. It can still run the extraction if
the reader asks ("run it anyway"); bmlib needs no override flag. *My call; the
maintainer agreed.*

## The text, and a paper too long for the model

Both calls take a `title` and a `text` (a `str`). Citation offsets are code
points into that `text`, the same unit as `find_passage` and
`CanonicalText`. StudyAnalyzer passes `CanonicalText.text`. Another app may
pass an abstract. *My call:* a plain string rather than a `CanonicalText`, so
the agent serves any caller and does not depend on the canonical package.

**Nothing is truncated** (the family's rule; StudyAnalyzer §6.4). Upstream's
extraction already refused to cut; its suitability check did cut, to 3,000
characters, and that is not ported. Text over the agent's budget is condensed
first, **exactly as `CochraneAssessor` does**: the same `ProcessingConfig`
budget (`condense_config`), the same `LLMChunkProcessor` map-reduce, the same
refusal of a failed, empty or still-oversized digest, and the same
`condensed_from_chars` / `condensation_status` on the result. The PICO
condensation prompt asks each batch for PICO evidence **as verbatim quotes**.
The extraction then runs once over the digest, and **every quote is verified
against the original `text`, never against the digest**. A quote the digest
paraphrased is caught there and its part lands under *Unverified*, like any
other. The suitability check uses the same rule.

For StudyAnalyzer, the suitability check reads only the abstract when the
canonical text has one, which keeps it cheap. That choice is the app's: it
passes the text it wants checked.

## The quote threshold

`find_passage`'s `min_similarity` is **a required argument of the extraction**,
with no default. StudyAnalyzer sets it by measurement in plan 2b (its design
§6.3); `find_passage` itself has no default either. *My call.*

## Failures

A failed call reports **why** in both languages, which differs from the
Cochrane assessor's `None` / `Option`. The causes are:

- **empty input**: the title and text are both empty, so no model call is
  made;
- **request failed**: a transport or provider error;
- **truncated**: the reply hit the output ceiling. The remedy is a larger
  `max_tokens`, not a retry or another model;
- **unusable reply**: an empty reply, one that does not parse, or one that
  lacks a required field (below);
- **condensation failed**: the condensation failed, or the digest still does
  not fit.

The app needs the cause: a transport failure is retried, while a model that
cannot produce valid JSON is a reason to pick another model. StudyAnalyzer is
already parsing log wording for the same reason elsewhere (bmlib #565). In
Python this is an exception type; in Rust it is an error enum. *My call.*

**How Python learns the cause.** Rust's `chat_json` already reports it through
`ChatJsonError` (`Truncated`, `WrongShape`, `Empty`, `Unparseable`, request
failed). Python's `BaseAgent.chat_json` raises a bare `ValueError` for all of
these, with only the message differing. It gains **subclasses of
`ValueError`**, one per Rust variant, so the PICO agent catches types rather
than matching messages. Every existing `except ValueError` keeps working, so
the Cochrane assessor and Tiers 2 and 3 do not change. A provider exception
that escapes `chat_json` maps to *request failed*. *My call.*

**Required fields.** In the extraction, all four parts must be present, each
as an object with a string description. A reply missing one, or carrying the
wrong type, is the wrong shape. The agent retries it in its own loop, as the
Cochrane assessor retries an unusable parse (`_ASSESSMENT_ATTEMPTS`, 2), since
`chat_json`'s shape check in either language tests only object against array.
If those retries run out, it is an *unusable reply*. A part is
never silently dropped and never invented. In the suitability check, only
`is_suitable` is required, and it must be a boolean. The other five fields are
read as unstated when absent or of the wrong type, so they are optional in the
result type. This replaces upstream's rule that a missing field among six
fails the call. *My call.*

A malformed value **inside** a reply that is otherwise usable is read under
`quality/_json_fields`' rule (annotated type, or unstated), like every other
quality reader:

- a confidence outside 0–1 is clamped, as `_clamped_confidence` does;
- a quote that is not a string is dropped and counted;
- a `reported` flag that is not a boolean counts as reported.

## Python and Rust

The Rust module is a port of the Python, as the Cochrane assessor's is. The
prompts are byte-identical in both. They are held together by the existing
oracle machinery. A Python dumper (`rust/oracle/dump_pico_agent.py`, beside
`dump_cochrane_assessor.py`) runs the agent over scripted model replies and
texts. It writes cases and expected results to `rust/bmlib/tests/data/`.
`scripts/rerun_rust_oracle.py` learns the new dumper, and a Rust test asserts
every case.

This is the first dumper to run a **whole agent** over scripted replies. The
existing ones pin a parser and the prompt text on fixed inputs. `chat_json`'s
retry pauses must not slow the dumper; how to avoid them is the builder's
call. The cases cover:

- every part state;
- a found and a lost quote in one part;
- a malformed reply;
- an empty input;
- a condensed run;
- the suitability answers.

The request messages are part of each expected result, so a prompt that
drifts in one language fails the oracle.

**Upstream's 27 tests are the starting oracle.** Those that test behaviour
kept here are ported, and rewritten where this design changes it: the
confidence filter, the truncation, and the result for a missing field. Those
covering the dropped surface (batch, export, stats, the summary formatter) are
not.

## Non-goals

- The batch loop, CSV/JSON export, statistics counters and the text summary
  formatter of upstream. These are app concerns.
- Storing runs, re-running with another model, and the card's wording. These
  belong to StudyAnalyzer plan 2c.
- Choosing the `find_passage` threshold. That is plan 2b's measurement.
- Streaming the extraction. The tab shows the finished analysis.
- Quotes for study type and sample size, which upstream returns as plain
  strings. *My call:* they stay plain strings. Study type is a judgement, and
  the four parts are what the maintainer asked to be verified.
- An FFI binding.

## Anti-goals (a failure even if it works)

- A part shown as verified when none of its quotes was found in the paper.
- A citation that stores the model's copy rather than the paper's span.
- Any truncation of the paper, on either call.
- A part silently dropped, or a low-confidence extraction thrown away.
- Rust and Python returning different results for the same model replies.

## Left to the builder

- Module and type names (*my suggestion*: `bmlib/quality/pico_agent.py` and
  `pico_models.py`, beside the Cochrane assessor).
- The exact prompt wording, starting from upstream's, with the additions
  this design needs: the quotes, the `reported` flag, and the verbatim-quote
  condensation prompt.
- How many quotes per part the prompt asks for.
- The JSON reply shape.
- Whether quote verification is a separate public function. It is useful to
  StudyAnalyzer's Interrogator, but nothing here requires it.
- The `CHANGELOG`, manual, ROADMAP and HANDOVER entries, as every session
  writes them.
