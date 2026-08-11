# Shipping v0.3 — synthesis, README, SPEC bump, tag

**Date:** 2026-08-11
**Track:** v0.3 generation ergonomics — **close-out**. No code, no
language change, no new measurement.
**Status:** design approved in session; implementation plan follows.

The v0.3 closing-baseline REPORT ends by declaring this work out of its
own scope: *"Shipping v0.3 itself (synthesis REPORT, README results
section, SPEC version bump v0.2.2 → v0.3, tag) is deliberately out of
scope for this task — it follows this campaign as its own piece of
work."* This is that piece of work.

## What v0.3 turned out to be

Three changes shipped under the version number: SPEC §55 (`vec(...)`
variadic list literal), §56 (field assignment `s.f = e`), §57 (`to_str`
as a second name for `int_to_str`). Each was chosen from a measured
friction in the G0 corpus rather than proposed from taste.

Three campaigns bracket them, all constrained, all 3 families × 3 arms ×
20 tasks × 10 seeds (n=200 per arm per family):

- `g0c` — `eval/results/g0-generation-baseline/constrained`, pre-change
- `g1c` — `eval/results/g1-vec-literal/constrained`, after §55 only
- `v03c` — `eval/results/v03-closing-baseline`, after all three

### The finding: one change did the work

Oxide-arm first-pass rate, percentage points:

| family | g0c | g1c | v03c | §55 (g0c→g1c) | §56+§57 (g1c→v03c) | total |
|---|---|---|---|---|---|---|
| qwen | 26.0 | 29.0 | 30.5 | **+3.0** | +1.5 | +4.5 |
| codegemma | 14.5 | 16.5 | 16.5 | **+2.0** | +0.0 | +2.0 |
| granite | 9.0 | 9.5 | 9.5 | **+0.5** | +0.0 | +0.5 |

§55 accounts for **5.5 of the 7.0 points** summed across families
(79%). The remaining 1.5 is one family's +3 sessions of 200, against
SPEC §47's ±5pp resolution floor for this design.

**First-compile is the sharper view, and the synthesis leads with it.**
An ergonomic language fix acts on whether a program compiles; passing
additionally requires the algorithm to be right, which no syntax change
can supply. On that metric the same decomposition is more concentrated
still — and §55's share is **dose-ordered in its own measured demand**:

| family | g0c | g1c | v03c | §55 | §56+§57 | §55 demand (pre-change `vec` calls) |
|---|---|---|---|---|---|---|
| qwen | 30.0 | 34.5 | 36.0 | **+4.5** | +1.5 | 91 |
| codegemma | 20.0 | 23.5 | 23.5 | **+3.5** | +0.0 | 69 |
| granite | 31.0 | 33.0 | 33.0 | **+2.0** | +0.0 | 27 |

§55 takes **10.0 of the 11.5 first-compile points** (87%), and its
per-family effect orders exactly as the demand counts do (91 > 69 > 27 →
+4.5 > +3.5 > +2.0). A dose-response across independent families is the
same evidentiary form that carried §53, and the opposite of the pattern
that sank three earlier headlines here. The demand counts are the
pre-change `vec`-call figures from `eval/results/g1-vec-literal/REPORT.md`
(91 → 21, 69 → 13, 27 → 6).

Both controls stay flat across the whole of v0.3 (g0c→v03c):

| arm | qwen | codegemma | granite |
|---|---|---|---|
| rust | −0.5 | 0.0 | 0.0 |
| explicit | +1.0 | −0.5 | −0.5 |

The explicit arm is the more informative of the two. It receives §55,
§56 and §57 identically — they are core front-end changes both dialects
inherit — and does not move. That reproduces, on generation, the same
split the repair probe found for §53: ergonomic fixes lift the implicit
arm and not the explicit one, whose binding constraint is its annotation
burden.

### What §56 and §57 bought instead of rate

Neither is written off, and neither is upgraded into a rate claim it
did not earn:

- **§56** eliminated an instrument artifact. The statement-position
  `f.x == e` deformation signature went **18 → 0** across all 600
  oxide-arm first attempts (pre-change: codegemma 10, granite 6, qwen 2,
  in 9 programs), verified by re-running the identical tool against the
  committed G0 root. Its implementation also surfaced the
  intermediate-segment defect class — `o.i.clone().n = 5` — the only
  class this project has found that **rustc accepts** and therefore
  escapes the oracle entirely.
- **§57** could not have moved rates, and the campaign proved it rather
  than assuming it. Of the programs reaching for `to_str`, **0 of 9
  compiled pre-change and 0 of 11 after**; five of the nine die in the
  parser, the rest on unrelated name clashes, and no diagnostic anywhere
  reports `to_str` as unresolved. The true effect on first-compile was
  bounded at **0.0pp**, not merely below the floor.

### The conclusion this licenses

Cost per point is rising: §55 bought a mean of +1.83pp across the three
families; §56+§57 together bought +0.5pp for more implementation and
review work than §55 required. Combined with §57's mechanical null —
resolution-stage ergonomics sit downstream of where these models
actually fail — the measured reading is that **the ergonomics loop has
reached diminishing returns**, and the remaining conversion-name
candidates in taxonomy dossier 3 are not worth SPEC surface. That is the
evidence behind the direction memo's pivot to the fine-tune track
(SPEC §32.4), for which `v03c` is the comparison point.

## Deliverable 1 — synthesis REPORT

**Path:** `eval/results/v03-synthesis/REPORT.md` (new directory).

Permitted: the standing constraint forbids **modifying** anything under
`eval/results/`, not adding to it. This is the first non-campaign entry
there, so it opens with a header stating that it has no `cells.jsonl` of
its own and naming the three campaign roots it reads.

**It must carry:** the first-compile table (leading, with the
dose-response), the first-pass table, and the controls table; the
§55/§56/§57 accounting; the 87% and 79% figures; the
diminishing-returns conclusion and its link to the pivot; and a "what
this does not show" section.

**Every rate in it must be reproduced, not recalled**, by running
`eval.g0_report` against each of the three committed roots — the exact
three commands are in the implementation plan, and all three were
verified to reproduce the tables above before this design was written.

**It must not duplicate** the closing-baseline REPORT. That document
owns the endpoint scorecard, the comparability statement, the
provenance, the four-hour method note, and the two VACUOUS endpoints;
the synthesis cites it rather than restating it.

**One caution it must state.** At v0.3 HEAD the paired oxide − explicit
**generation** first-pass deltas are qwen +9.0pp (SE 6.8), codegemma
+4.5pp (SE 3.7), granite +1.0pp (SE 4.6) — **none clears 2 SE**. The
project's headline ownership result is a *repair* result. A reader
arriving at the v0.3 close-out must not be able to mistake the two, and
this is the document where they meet.

## Deliverable 2 — README results section

Three edits, all in `## Where the evidence stands`.

**A. Split the fix accounting by what it measures.** `### Two ergonomic
fixes, and what each was worth` (line 143) becomes `### Ergonomic fixes,
and what each was worth`, holding two separately labelled tables:

- *Measured on repair* — the existing §53/§54 rows, unchanged.
- *Measured on generation (v0.3)* — a new table carrying the oxide arm's
  **first-compile and first-pass** deltas with the g1c split above, so
  the dose-response is visible and the "one change did the work"
  conclusion is checkable from the README alone.

The two metrics never share a column. Mixing a repair delta and a
generation delta under one "effect" heading is the false-comparability
error that produced three withdrawn headlines here.

**B. Refresh the stale HEAD rates.** Line 201 reads *"Under constrained
decoding at HEAD, first-attempt pass rates are 26 / 14.5 / 9% for Black
Oxide against 57 / 45 / 42% for Rust"*. Those are `g0c`. At v0.3 HEAD
they are **30.5 / 16.5 / 9.5** against **56.5 / 45 / 42**, with the g0c
figures retained as the before-state. The bullet's claim is unchanged in
direction and gets stronger in precision.

**C. Fix a misattributed frontier figure.** The same bullet — about
**writing** Black Oxide — cites *"a frontier model prefers Rust 100 to
92."* That is 12/12 vs 11/12 from `eval/results/ownership-probe-frontier/`,
which is the **repair** probe. The frontier *generation* evidence is in
that report's own closing line: all three arms scored **20/20**. The
citation is corrected to the repair probe it comes from, and the
generation bullet states the frontier generation result it actually has
— a tie at ceiling, which supports the bullet's point about pretraining
exposure at least as well.

## Deliverable 3 — SPEC

No normative change. Two edits plus their cross-references.

**A. Part XI header.** `# Part XI — Builtin Method Syntax (v0.2.2)`
(line 2379) becomes `# Part XI — Generation Ergonomics (v0.3)`. The part
now spans §53–§57 — method syntax, `mut`, vec literals, field
assignment, a builtin alias — and every one of them was added because a
measured generation friction demanded it, which is what the new title
says. Two in-repo references to the old title are reworded to name §53
specifically: SPEC §32 line 1350 (*"Part XI method syntax, +42pp"*) and
README line 114. The two committed REPORTs citing "SPEC Part XI §53" and
"§55" remain accurate and are not touched — they are under
`eval/results/`.

**B. Close the roadmap's v0.3 entry.** §32 item 3 currently makes "v0.3"
mean only the **rejected** ownership inversion; SPEC has no record of
what actually shipped under the number. The item gains a closing
paragraph: v0.3 as shipped is the ergonomics track (§55–§57), its
measured outcome, and a pointer to the synthesis REPORT — recorded in
the same idiom the item already uses for the rejected proposal it
retains for the record.

## Deliverable 4 — tag

Annotated tag `v0.3` on `main`. **The repository has no tags at all
today**, so this is the first; no retroactive tagging of v0.2.x is in
scope. The message names the three sections shipped and the synthesis
REPORT path.

## Constraints

- **Frozen surfaces must not be edited:** `LANGUAGE_CARD.md`,
  `LANGUAGE_CARD_EXPLICIT.md`, the `OX0306` suggestion string,
  `ARMS`/`arm` data keys, the `__oxide_` codegen prefix, `.ox`,
  `eval/grammar/oxide.gbnf`. A card edit retokenizes every prompt and
  destroys comparability with all three campaigns.
- **Nothing under `eval/results/` may be modified.** Adding
  `v03-synthesis/` is the only permitted touch.
- **No new measurement.** Every number in every deliverable is read from
  committed data or from a committed REPORT, and each is cited to its
  source.
- Commits carry no Claude/AI attribution.

## Out of scope

The fine-tune track itself (SPEC §32.4); any v0.4 language work; the
parked `BUILTIN_REF` sync test; `eval/grammar/build.py`; the two
untracked `eval/results/g0-generation-baseline/*/samples/` directories,
which predate this work and must not be swept in by a bulk `git add`.

## Verification

Full suite green (1446 tests at branch point — the card and rollup tests
are the ones that would catch an accidental frozen-surface edit), every
cited figure traced to its committed source, and `git tag` showing `v0.3`
on the merge commit.
