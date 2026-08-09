# g1 — the `vec(...)` literal, measured

**Date:** 2026-08-09
**Change under test:** SPEC Part XI §55 (`52c7487`): `vec(a, b, c)`
desugars at parse time to the `push` chain. Pure sugar, both dialects,
cards deliberately UNCHANGED (models already wrote the form unprompted;
this measures pure acceptance). Taxonomy dossier 1.
**Design:** the constrained G0 grid re-run at the new HEAD — 3 families
× 3 arms × 20 tasks × 10 seeds, GBNF-constrained, same seeds, same
windows (granite 4096), prefix `g1c`. Before = `g0c` (committed).
Predictions were pre-registered in the taxonomy before implementation.

## The prediction scorecard

**Primary — CONFIRMED with the full dose-response.** First-attempt
`OX0303` (oxide+explicit):

| family | predicted | measured |
|---|---|---|
| qwen | ≈ −60 | 91 → 21 (**−70**) |
| codegemma | ≈ −50 | 69 → 13 (**−56**) |
| granite | ≈ −15 | 27 → 6 (**−21**) |

All three families, in the predicted order, with the predicted shape:
the counter fell hard while pass rates rose less ("freed programs fail
later stages"):

| family | oxide first-pass | oxide final-pass | explicit first-pass |
|---|---|---|---|
| qwen | 26.0 → **29.0** | 29.5 → **34.5** | 20.5 → 22.0 |
| codegemma | 14.5 → **16.5** | 16.0 → **18.5** | 12.5 → 12.0 |
| granite | 9.0 → 9.5 | 11.5 → 12.0 | 9.0 → 9.0 |

**"rust arm byte-flat" — holds at the measurement level, and the byte
claim was too strong.** Rust first-compile and first-pass rates are
IDENTICAL in all three families; final-pass wobbles by exactly one
session per family (repair-tail). Byte level: 48 of ~1,470 rust raw
files differ across the two runs (7 of 600 first attempts, zero
first-attempt verdict flips) — llama-server sampling at temperature 0.8
is not byte-deterministic ACROSS SERVER INSTANCES, only near-so. The
internal control is henceforth a rate-level claim at the first-attempt
layer (where this A/B reads its codes), not a byte-level one.

**"EX0002 untouched" — WRONG as written, for a legible reason.** qwen's
explicit-arm EX0002 went 1 → 22. The annotation CHECK is untouched; the
POPULATION reaching it grew — programs the arity error used to kill now
progress into the explicit dialect's annotation rules. This is the
freed-programs-fail-later mechanism showing up inside the explicit
arm's own code space, and it slightly depresses the explicit arm's
measured gain from a change both arms received.

**Unpredicted: granite's `OX0203` spiked 310 → 498.** Sampled: the
dominant shape is granite's known degenerate whole-program repetition —
a complete program followed by a second complete program (duplicate
`fn main`, duplicate helpers) — now with more room to run because
vec-freed programs die later. An amplification of a known
model-competence pattern, not a new language friction. (The samples
also surfaced a demand not in the top dossiers: `2.to(x / 2)` as a
range method — noted for the taxonomy, low counts.)

Other counters moved little (qwen OX0200 274→275, OX0306 127→133;
codegemma OX0306 179→198 — the conversion-method demand of dossier 3
persists untouched, as expected). The gate stays empty (1 OX04xx
occurrence). Exhaustion unchanged (granite 100 both runs).

## Reading

The loop's first fix behaved exactly as the §53/§54 template promises:
a pre-registered, family-ordered dose-response on the targeted counter,
a real but smaller pass-rate lift, honest side-effects where programs
progress deeper. qwen's oxide final-pass gained **+5.0pp** from one
line of parser sugar. The next ranked candidate (dossier 2, field
assignment) is feature-class and needs its SPEC extension first;
dossier 3 (conversions) remains fully live — codegemma's OX0306 even
grew.

## Limits

- One iteration on the same 20-task corpus; corpus-concentration
  caveats from the taxonomy apply unchanged (t19/t20 drive most
  OX0203).
- Cards unchanged by design — a card mention of `vec(a, b, c)` is a
  separate, measurable lever deliberately left unpulled.
- granite numbers carry the 4096-window covariate and its 16.7%
  exhaustion rate throughout.
- The g0c↔g1c comparison spans two server boot cycles; see the rust
  byte-determinism note above for what that does and does not permit.

Raw: `constrained/` (30 run dirs). Analysis: `python -m eval.g0_report
--root eval/results/g1-vec-literal/constrained --run-prefix g1c ...`
