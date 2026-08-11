# v0.3 synthesis — what three ergonomic changes bought

**Date:** 2026-08-11
**Type:** synthesis. **This directory holds no `cells.jsonl`, no run
dirs, and no raw model output of its own** — it is the first
non-campaign entry under `eval/results/`, and a reader should not go
looking for data here that is not there. Every figure below is derived
from three committed campaign roots:

| prefix | root | code state |
|---|---|---|
| `g0c` | `eval/results/g0-generation-baseline/constrained` | pre-change |
| `g1c` | `eval/results/g1-vec-literal/constrained` | after §55 only |
| `v03c` | `eval/results/v03-closing-baseline` | after §55, §56, §57 |

All three are the same design: constrained decoding, 3 families × 3 arms
× 20 tasks × 10 seeds, **n = 200 per arm per family**. Reproduction
commands are in *Sources* at the end; every number here was re-derived
from them for this document rather than copied from a prior REPORT.

## What v0.3 was

Three changes shipped under the version number:

- **§55** — `vec(a, b, c)` variadic list literal
- **§56** — field assignment, `s.f = e`
- **§57** — `to_str`, a second name for `int_to_str`

Each was selected from a measured friction in the G0 corpus rather than
proposed from taste. None of them is the change the version number was
originally allocated to: SPEC §32 item 3 reserved v0.3 for inverting the
ownership default, and that proposal was **rejected** at its gate on
2026-08-07. The ergonomics track inherited the number.

## The finding: one change did the work

### First-compile

First-compile is the metric an ergonomic change acts on. Passing
additionally requires the algorithm to be correct, which no syntax
change can supply, so pass rate dilutes the signal with model
competence. Oxide arm, percentage points:

| family | g0c | g1c | v03c | §55 (g0c→g1c) | §56+§57 (g1c→v03c) | total | §55's `vec` demand |
|---|---|---|---|---|---|---|---|
| qwen | 30.0 | 34.5 | 36.0 | **+4.5** | +1.5 | +6.0 | 91 |
| codegemma | 20.0 | 23.5 | 23.5 | **+3.5** | +0.0 | +3.5 | 69 |
| granite | 31.0 | 33.0 | 33.0 | **+2.0** | +0.0 | +2.0 | 27 |

**§55 accounts for 10.0 of the 11.5 points — 87%.** And its per-family
effect is **dose-ordered in its own measured demand**: 91 > 69 > 27
pre-change `vec` calls giving +4.5 > +3.5 > +2.0. The change helped each
family in proportion to how much that family had been asking for it.

A dose-response across independent families is the same evidentiary form
that carried §53's method-syntax result, and it is the opposite of the
pattern that sank three earlier headlines in this project — each of
which was a single-subject effect that dissolved under replication.
Demand counts are the pre-change figures from
`eval/results/g1-vec-literal/REPORT.md` (91 → 21, 69 → 13, 27 → 6).

### First-pass

| family | g0c | g1c | v03c | §55 | §56+§57 | total |
|---|---|---|---|---|---|---|
| qwen | 26.0 | 29.0 | 30.5 | **+3.0** | +1.5 | +4.5 |
| codegemma | 14.5 | 16.5 | 16.5 | **+2.0** | +0.0 | +2.0 |
| granite | 9.0 | 9.5 | 9.5 | **+0.5** | +0.0 | +0.5 |

§55 accounts for 5.5 of the 7.0 points — 79%. Mean per family: §55
bought **+1.83pp**, §56 and §57 together bought **+0.5pp**.

### The residual is one family, and the wrong one

The entire non-§55 movement in v0.3 is qwen's +1.5pp — **3 sessions of
200**, against SPEC §47's ±5pp resolution floor for this design.

There is a specific reason not to attribute even that to §56. If the
field-assignment fix were driving it, the family with the most
deformation to remove should gain the most. The opposite holds:

| family | §56 deformation removed (stmt occurrences, g0c) | g1c→v03c first-compile |
|---|---|---|
| codegemma | **10** (the most) | **+0.0** |
| granite | 6 | +0.0 |
| qwen | **2** (the fewest) | **+1.5** |

The family that moved is the one with the least artifact to remove, and
the family with five times its artifact did not move at all. That is the
signature of noise, not of a dose-response. The honest reading of the
+1.5 is run-to-run variation — the same ±1–3 session wobble the closing
baseline documents across 27 cells at temperature 0.8.

## Controls

First-pass, g0c → v03c:

| arm | qwen | codegemma | granite |
|---|---|---|---|
| **oxide** | **+4.5** | **+2.0** | **+0.5** |
| explicit | +1.0 | −0.5 | −0.5 |
| rust | −0.5 | 0.0 | 0.0 |

Rust is the external control and is flat, as pre-registered. First-compile
is *identical* in all three families across all three campaigns
(98.0 / 84.5 / 80.0 throughout).

**The explicit arm is the more informative control.** It receives §55,
§56 and §57 identically — these are core front-end changes and both
dialects inherit them — so if the gains were generic "the language got
easier" effects, explicit should track oxide. It does not. On
first-compile, summed across families, oxide gains **+11.5pp** against
explicit's **+1.5pp**, a factor of roughly eight. Explicit is not
motionless (qwen's explicit first-compile moves 23.0 → 24.5), and
claiming it were would overstate the case; it moves an order of
magnitude less.

This reproduces on *generation* the split the repair probe found for
§53: ergonomic fixes lift the implicit arm and not the explicit one,
whose binding constraint is its annotation burden rather than its
syntax.

## What §56 and §57 bought instead of rate

Neither is written off, and neither is promoted into a rate claim it did
not earn.

**§56 eliminated a measurement artifact.** Under grammar-constrained
decoding a model cannot be rejected — it is steered to the nearest legal
string — and with no field-assignment production available, `f.x = e`
was being deformed into the comparison `f.x == e`. That signature is
gone outright:

| corpus | statement-position | tail-position | programs |
|---|---|---|---|
| `g0c` | **18** (cg 10, gr 6, qw 2) | 17 (cg 1, gr 12, qw 4) | 9 |
| `v03c` | **0** | **0** | **0** |

Not reduced — eliminated, in both positions, across all 600 oxide-arm
first attempts. Its implementation also surfaced the intermediate-segment
defect class (`o.i.clone().n = 5`), the only class this project has
found that **rustc accepts** while executing the wrong program, and
which therefore escapes the accepted-implies-compiles oracle entirely.

**§57 provably could not have moved rates.** Of the programs reaching
for `to_str`, **0 of 9 compiled before the alias existed and 0 of 11
after**. Five of the nine die in the parser; the rest fail on unrelated
top-level name clashes; no diagnostic in any of them ever reported
`to_str` as an unresolved name. The largest possible effect on
first-compile was **0.0pp** — a mechanical bound, not a statistical one.
The wall these programs hit is the lexer and the parser, and
resolution-stage ergonomics sit downstream of it.

## The conclusion

§55 bought a mean +1.83pp first-pass and a dose-ordered +3.33pp mean
first-compile. §56 and §57 together bought +0.5pp, which the section
above argues is not theirs, for more implementation and review work than
§55 required. Add §57's mechanical null — an entire class of fix aimed
below where these models actually fail — and the reading is that **the
ergonomics loop has reached diminishing returns**.

Two consequences follow, and both are decisions rather than
observations:

1. The remaining conversion-name candidates in taxonomy dossier 3 are
   not worth SPEC surface. If marginal ergonomic effort continues at
   all, it belongs on truncation and parse failure, which is where the
   carrier programs above are actually dying.
2. The next phase is the fine-tune track (SPEC §32.4) rather than a
   fourth ergonomic fix. `v03c` is its comparison point, which is why
   that campaign was run as a baseline first and a hypothesis test
   second.

## A caution this document must carry

At v0.3 HEAD the paired **generation** first-pass deltas, oxide −
explicit, are:

| family | delta | SE | clears 2 SE? |
|---|---|---|---|
| qwen | +9.0pp | 6.8 | no |
| codegemma | +4.5pp | 3.7 | no |
| granite | +1.0pp | 4.6 | no |

**None of the three clears 2 SE.** The project's headline result —
implicit linearity repaired more reliably than explicit, in all three
families, with the effect resolving — is a **repair** result, measured
on a different instrument with a different corpus. On *generation*,
which is what this document reports, the same comparison does not
resolve at this sample size.

These two must not be conflated, and this is the document where they
meet.

## What this does not show

- **One shot condition, one 20-task corpus, one temperature.** SPEC §47's
  power analysis puts this design's resolution at roughly ±5pp. Every
  per-family delta reported here except §55's qwen first-compile sits
  below that floor; the §55 conclusion rests on the **dose-response
  across three independent families**, not on any single cell clearing
  significance.
- **§56 and §57 landed in one campaign and cannot be separated by it.**
  Every g1c→v03c figure is joint. The argument above that the residual
  is not §56 is an inference from the wrong-family pattern, not a
  measurement of §56 alone.
- **granite carries its 4096-window covariate throughout** (SPEC §48 —
  8192 exceeds its own training context), with a ~16.5% context-
  exhaustion rate, inherited unchanged across all three campaigns.
- **No new data was collected for this document.** It is arithmetic and
  reading over three committed corpora.
- **The v03c grammar differs from g0c's and g1c's**, because §56 added
  field-assignment productions to both dialect grammars. That delta is
  the mechanism under test in the deformation result, not an incidental
  confound; g0c and g1c share byte-identical grammars. The closing
  baseline's comparability statement documents this in full.

## Sources

The three campaign REPORTs are authoritative for their own runs, and
this document defers to them rather than restating them — in particular
`eval/results/v03-closing-baseline/REPORT.md`, which owns the
pre-registered endpoint scorecard (including two endpoints recorded as
**vacuous**), the comparability statement, the provenance table, and the
method note on four idle hours mid-campaign.

```bash
# rates — the three campaigns
.venv/bin/python -m eval.g0_report --root eval/results/g0-generation-baseline/constrained \
  --models qwen7b,codegemma7b,granite8b --seeds 1-10 --run-prefix g0c
.venv/bin/python -m eval.g0_report --root eval/results/g1-vec-literal/constrained \
  --models qwen7b,codegemma7b,granite8b --seeds 1-10 --run-prefix g1c
.venv/bin/python -m eval.g0_report --root eval/results/v03-closing-baseline \
  --models qwen7b,codegemma7b,granite8b --seeds 1-10 --run-prefix v03c

# §56 deformation signature, before and after
.venv/bin/python -m eval.deformation eval/results/g0-generation-baseline/constrained
.venv/bin/python -m eval.deformation eval/results/v03-closing-baseline
```

Paired deltas and their standard errors are printed by `eval.g0_report`
itself (via `rollup.paired_delta` / `paired_se`). The `vec` demand counts
are from `eval/results/g1-vec-literal/REPORT.md`; the `to_str` carrier
analysis is from `eval/results/v03-closing-baseline/REPORT.md`.
