# Session rules — what a session gets wrong again

_Lifted out of `HANDOVER.md` on 2026-09-17, where it had grown to a 172-line
section. It lived there because every session needs it and none of it is
derivable from the code; it lives here because HANDOVER is meant to answer
"what is the state and what is next". The move was verbatim apart from the
section's opening paragraph, which the one below replaces; rules PR #280 added
beside the moved ones are additions, each naming the issue or review that
earned it._

**Each rule is argued in full in [`CLAUDE.md`](../CLAUDE.md), in
[`docs/DECISIONS.md`](DECISIONS.md) and at its call site; only the
shortest form is kept here**, because these are what a session gets wrong again
rather than what it can look up. Several are now enforced by a test rather than
by prose — `TestTheAuditNetIsComplete`,
`TestOnlyAnAccumulatingElementReadsTheBuffer`,
`TestEverySectionIsGatedOnEveryCounterItReads`,
`test_every_call_site_passes_the_parts_it_built` — and those are kept anyway,
since the rule is what tells you why the test is there.

**Add to this file when a review teaches a rule, not to `HANDOVER.md`.**

*Evidence.* A rule's population can be large, empty, or both, and only a draw
says which; one window is not the rate (#127 read 0 of 662 recent tables and 11
of 93 in a 1996-1998 draw). **An issue's own remedy is a hypothesis too** —
#162's cost ten minutes to refute, #183's was refuted by 1,750 articles ending
in a legal trailing comment. **Measure the population the code actually
reads**; prefer a corpus with a public name over one on your disk, and check
that its *rendition* is the one the code is fed (#138). **A live Europe PMC
draw must be stratified by source and publication year**, a cursor page being a
contiguous block of accessions. **Run one live probe at a time** — the per-host
pacer is per-process (#179). **A share is of a denominator, and the rendition
chooses the denominator** (#164). **Probe the contract, not the expression the
reporter noticed** (#199). **State a blast radius from a diff, not from the
call graph** — and the diff's own predicate is a claim to check: prefix where
the honest test was *subsequence* (#224), a `difflib` opcode walk aligning
arbitrarily over a list whose every member changed (#243). **Load both
checkouts in one process** where a corpus makes two dumps expensive, after
validating that comparator on the smaller artifact. **The harness that produces
a blast radius is itself an instrument**, and **a gap between two of bmlib's
own counts is a defect in one of them until it is explained** — reconcile a
routing tally against the diff **per article**, not in total: #230's served gap
of 4 was one article's four empty paragraphs, and the total hid which. **A
mirror over the markup and a tally from the routing are different instruments,
and where they disagree the routing is the finding**; where the code has a
predicate, run the code, and **measure a drop at the drop** — #230's instrument
used the parser's own predicates and checked every classified run against a
before/after fingerprint of every destination, which is what made "0
mismatches" a result rather than an assumption. **Separate what is already
filed from what is lost**: 21,225 served `<p>` in table cells fall past
`_append_prose` too, and `characters()` has filed every one. **A blast radius
must show nothing was lost, not only that the new value appeared** (#265's
first diff missed a lost citation that way). **Assert the number a log line prints,
not that it printed.** **A container you describe in prose is a claim too.**
**A committed corpus is not the only honest population**, but nothing in the
suite re-derives the two named artifacts — state the trade. **A count that
reads as a population but is a subset is the recurring mis-statement** (#274's
1,047 row quoted as the counter's 1,105 in five files). **An issue's published
table is an instrument's output too**, and **so is a reviewer's**: PR #280's
round quoted a review's "382 articles" split by a `<kwd-group>` into three
docstrings and a commit, and the per-article diff said 71. **A count that
matches a shape does not attribute the population to it** — the same round
credited the review's "9 articles, 10 pairs" of doubled headings to a
nested-element shape that measures 0; classifying the pairs said sibling
`<notes>`. Reproduce it before quoting it, and say
which columns reproduced — #268's served and `PMC001xxxxxx` columns reproduced
exactly and its archive column did not, which is a fact about two scripts and
not about bmlib. **Where a measured distribution is smooth, a threshold on it
is an unmeasured constant** — that is what refused #268's coverage test rather
than taste. **A survey can
refuse part of a remedy**, not only size it, **and it can find the issue beside
the one you took**: #230's tally surfaced #234's empty headings (already filed)
and #253.

*Rules and their neighbours.* When a rule replaces a guard, ask what else that
guard was holding — and **a guard you add can widen the defect next door**:
#272's empty-`<fpage>` guard kept `pages` non-empty, which re-admitted the
`100-101-201` range an `<lpage>` appends to a closed one, so a blank became a
corruption. Ask what the *old* behaviour was accidentally hiding. **A guard whose reason moves needs its comment moved with
it** — #230 made the object-metadata refusal load-bearing for 19 archive
licences that had been falling past every branch anyway. **An `Any`-returning
helper launders every annotation above it.** When a fix extends a routing rule,
walk every other path it reaches — the guard written on one branch is the
guard the others need. **An issue's suggested remedy can be narrower than the
rule it invokes** (#243's `characters()` hold). **A fix that removes a corrupt
survival can create a total loss elsewhere** — measure it and count it (#245).
**Before discarding an element, find where its text lands on `main`**, off the
real handler's `text_stack`. **Isolating a buffer answers the children that
merge, not those that route or write a builder directly** — name the routes by
kind and guard each; **an exception is not made until it reaches every route**
(#241/#248's `<xref>` exception lived at the buffer pop alone). **A set keyed on
the element cannot express a rule about the context.** **Suppressing a merge
does not empty a buffer.** **A counter's scope is a decision, and the deposit
survey is what makes it**; **say which guard makes which exclusion**. **An
empty deposit costs nothing.** **When a fix routes a container, find every test
that used that container as *the* example of the old behaviour** — #230 moved
three tests and a parametrised row onto `<floats-group>`, the shape that still
has the property, rather than flipping them into duplicates. **Ask once the
numbers are in** when scope is a modelling choice: the routing question for
#230 went to the maintainer with the owner table in front of them.

*Diagnostics and tests.* **A design change re-decides which tests
discriminate** — PR #280's lazy flush made three guard fixtures, written to
separate their guards from their mutants under the eager flush, pin
behaviour instead, because a heading titling nothing stopped leaving any
trace. Work that out before the sweep, and re-describe the tests rather than
leave them claiming a discrimination they lost. **Ask what the guard *decides*, not what it is named
after** — #231's eight first-sweep survivors were all tests asserting the right
outcome for a fixture in which the mutant changes nothing, because a heading
admitted in a refused position dies at its own element's close and never titles
anything; under the eager flush what those guards decided was a *boundary*, so
the fixture needed routable prose either side (under the lazy flush they decide
nothing, per the rule above). **A stand-in for an imbalance has to reach the
field it is aimed at**: one dropped end tag cannot strand a depth-matched
frame, the residual shifting every later depth by one so the walk to the root
still passes through the owner's — it strands only once the residual exceeds
that depth. A diagnostic's *level* is a claim that has to be
measured, **and the branch it sits on must be no wider than the draw** (#191).
**A branch that is several populations has to be split before it can be
levelled** (#218). **When a fix takes a counter's only measured population,
the counter needs a test of its own or it goes vacuous the same day** (#224,
#230's `definition_terms_dropped`). **A status enum member is a stored claim.**
**Before arguing about a level, check the diagnostic exists** (#193). A net
needs its own false-positive net, and it must be free — the autouse
`parser_log` fixture makes every pre-existing fixture one. Key a counter on
*structure*, and **read the increment site, not the name or the report**.
**Asserting that a constant was imported is not asserting that it is used.**
**A count passed to a shared rule is a claim as much as a list of field names
is** — mechanise the call site, not only the rule (#268).
**A rule enforced by prose is not enforced** (`TestTheAuditNetIsComplete`,
`TestOnlyAnAccumulatingElementReadsTheBuffer`,
`TestEverySectionIsGatedOnEveryCounterItReads`). **Checking the arithmetic is
not checking the rule**, and check the denominator too. **A zero over an
absent population is not a clean result.** **Ask which line of the fixture the
assertion depends on.** **Mutate the *old* half of a condition you extend**,
and give a fixture prose *after* the close. **A guard's mutant can be inert for
the very fixture that names it.** **A surviving mutant is sometimes an unmade
decision.** **A membership is invisible wherever a sibling member is also in
the walk's path.** **Aim mutants at what a guard *reads*.** **An equivalent
mutant is not an untested guard** — and **an equivalence is a claim about the
code around the flag, so a later commit to that code re-opens it**: `in_body`
in `_prose_reaches_output`'s section conjunction is equivalent because the
fallback answers it too, and `in_front` was, until the next commit gave the
fallback a `<ref-list>` test for `<front>`; the claim rode into seven documents
unre-run, and four of PR #256's five reviewers independently found the mutant
losing an `<attrib>` with every test green. **Run a control mutant on the neighbouring flag you
did not touch**: #230's `in_back` control survived, a pre-existing guard nothing
pinned. **A nested fixture is the only one that sets two container flags at
once**, so it is the only one that can pin an order. **A contract net is blind
to a value read *wrongly* without raising** — assert what the run
*concluded*. **Pick the fixture that separates the guard from its own mutant**;
**a malformed fixture reads as a measurement**. **A measured *majority* is an
argument against a diagnostic** (#235) and **a measured-empty population is an
argument for closing an issue, not for building it** (#204, #207, #210).
**Run a correctness review and a claims review before the PR.**

*Owner rules.* **A comment naming which elements can reach a branch is a claim
the Tag Library settles** — #231's pop said `</front>` was the only owner whose
arm flushes and that reaching it needed invalid markup, where `<title>` may be
contained in `<back>` and may *not* in `<body>` or `<front>`. **Read the Tag
Library's "May be contained in" for every element
an owner rule names** — #254's first cut refused `<volume-issue-group>` and
`<pub-date><string-date>`, both the article's own and both legal, and passed
every test because no artifact deposits either. **Check old DTDs too**: NLM 2.x
deposits `<journal-title>` bare, the majority spelling in the back-files, which
neither named artifact contains. **A guard's *mechanism* is a claim**: the
nested-article guard was written up as keeping a round's values off the
article, when `characters()` is suppressed there and what it stops is the
closes *blanking* the article's fields. **A comment's measured count goes stale
when neighbouring tests change** — re-measure `main` as well as the branch
(the pop comment's 58/65 were 179/191 on `main` already). **A root anchor and a
suffix differ only under a wrapper**, and NCBI efetch sends one.

*Live behaviour.* **A remote's error shape is a property of the *request*, not
of the endpoint** (#218). **A property only a real remote can refute needs a
real probe** (#194). **A sampler must address *and head* requests exactly as
the code does.**

*Instruments.* **Diff two candidate designs against each other, not only
each against `main`** — eager against lazy is what gave #231's exact 71
articles and 0 HTML moves, which two diffs against `main` could only bound.
**Detach a long measurement** (`nohup`, a driver writing marker files, a
monitor that also reports the driver dying): a background wrapper shell was
killed mid-run while its Python children kept running, so the chained
archive phase never started and the monitor waited on a marker the dead
shell would never print. **A two-checkout comparator must compare by *value*, never with
`==`** — the two checkouts define different classes and `dataclasses.__eq__`
returns `NotImplemented` unless `other.__class__ is self.__class__`, so every
field holding a dataclass compares unequal for every article; #231's first run
reported six fields moving in ~80% of the corpus under a change that cannot
touch any of them. **Give a diff harness a self-check that fails when it
cannot see an *unchanged* field**: a diff reporting everything and a diff
reporting nothing are equally useless, and only one of them looks wrong.
**Derive a comparator's field list from `dataclasses.fields`,
and print what it could not compare in the headline** — the first cut of this
session's reported "moved: 19" beside 6,870 articles it had silently failed on
(one wrong attribute name). **Validate it by reproducing a published column**
before believing its zeroes (PR #263's title 95 / volume 2 / pages 2).
**Two instruments agreeing to the article** — a markup survey and a routing
diff — is the strongest form of a population claim. **A zero in a second
window has a cause, and it is not always the obvious one**: the back-files'
zero here is not that a refused date is never first (it is, 111 times) but
that the next date states the same year. **A share is of the population the
counter counts**, which is not always the row you quoted: this counter counts
refusals *before* a year is found, so 1,105 articles, not 3,739 deposits.
**A mutant can be wrong** — one this session was placed below the suppression
it meant to defeat, and a control's pattern had moved with the fix, so both
read as evidence they were not. **A list an instrument declares must be
derived from the code it measures, not restated.** **One declared list can hide two rules.** **Where
an instrument is wider than the code, say so at the site and bound the cost.**
**An `ast` net that only *visits* some shapes fails open** — an unvisited call
site is an absent entry and a set equality cannot see one, so #268's walk
matched `ast.FunctionDef` alone and an `async def`, a module-scope, a `lambda`
or a class-body call was invisible. **Count the population first, then assert
the walk accounted for all of it.**
**A guard on the page cannot see a loss one level down** (#212). **Do not
background a mutation sweep beside anything that reads the same checkout** —
commit first, restore from the held string *and* a disk backup, clear
`__pycache__` after each restore. **And never let two sweeps overlap**: a
backgrounded sweep the harness reports as finished may still be alive and
writing, and in PR #289 one raced a foreground chunk, stranded a mutant twice
and reported *"pattern absent"* for an arm the other process had already
mutated — a verdict, not an error. **A stranded mutant restores from `git`,
and every verdict taken while a second sweep could have been alive is
re-run.** **A `ProcessPoolExecutor` script needs its `__main__` guard** on
macOS, where workers spawn and re-import it.

*Cost.* **A test that pins a decision is reversed, not deleted, when the
decision is** (#206, and two rows of #265's per-field test under #268). **The cost of a schema addition is not a constant** —
ask what the batch already costs (#198). **Check before pricing**: #124's
issue priced a `to_dict`/`from_dict` pair neither exhibit model has.

*Process.* **A closing keyword next to an issue number closes it, quotation or
not** — never reproduce the substring outside a PR body meant to close; describe
it or drop the `#`. **After every merge, diff `gh issue list` against what the
commit says it filed and fixed**, both ways. **Check the ROADMAP for an issue
filed beside yours**: #234 had been open for three sessions on the exact shape
#230's survey turned up, with the remedy already written.
