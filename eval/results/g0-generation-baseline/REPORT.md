# G0 — the whole-program generation baseline at HEAD

**Date:** 2026-08-09
**Design:** 3 families × 3 arms × 20 tasks × 10 seeds × 2 decoding
conditions = 3,600 sessions (≤4 submissions each), per the v0.3 design
(`docs/superpowers/specs/2026-08-08-v03-generation-ergonomics-design.md`).
Constrained = GBNF on the two Oxide arms, rust free by design.
Backend: llama.cpp (Vulkan, b1-4988f6e) for BOTH conditions.
qwen2.5-coder-7b / codegemma-7b (q8_0, n_ctx 8192); granite-code-8b
(q8_0, **n_ctx 4096** — its own training context; llama-server caps the
slot there. SPEC §48's per-family window rule was written for this).

## The gate verdict

**The linearity gate did not populate.** Across ~1,200 constrained
Oxide-arm first attempts: **3 `OX04xx` occurrences in 3 sessions**
(threshold: ≥30 pooled, ≥2 families). Generation still essentially
never reaches the feature the language exists for — but the wall MOVED,
and where it moved to is the finding.

## Constrained (the headline condition)

| family | arm | first-compile | first-pass | final-pass |
|---|---|---|---|---|
| qwen | oxide | 30.0% | 26.0% | 29.5% |
| qwen | explicit | 23.0% | 20.5% | 32.5% |
| qwen | rust | 98.0% | 57.0% | 59.5% |
| codegemma | oxide | 20.0% | 14.5% | 16.0% |
| codegemma | explicit | 20.0% | 12.5% | 14.0% |
| codegemma | rust | 84.5% | 45.0% | 52.5% |
| granite | oxide | 31.0% | 9.0% | 11.5% |
| granite | explicit | 20.0% | 9.0% | 11.5% |
| granite | rust | 80.0% | 42.0% | 53.0% |

Paired first-pass deltas (oxide − explicit): qwen **+5.5pp** (SE 6.3),
codegemma **+2.0pp** (SE 3.1), granite **+0.0pp** (SE 4.6). **None
clears 2 SE; granite sits at §47's no-signal-at-floor.** Constrained
whole-program generation shows no resolved implicit-vs-explicit
difference at this capability band — consistent with the repair probe's
finding that the ownership effect only resolves once everything else is
supplied.

Stage histogram, first attempts (oxide+explicit pooled per family):

| stage | qwen | codegemma | granite |
|---|---|---|---|
| lexer | 0 | 0 | 0 |
| parser | 29 | 46 | 54 |
| **resolve** | **399** | **620** | **818** |
| types | 325 | 336 | 209 |
| linearity | 1 | 0 | 2 |

The grammar eliminated the lexer wall outright (the pre-§53/§54
constrained probes' small parser residue remains — truncation cuts
mid-program). **The binding constraint is now name resolution, with
types second.** Code-level, first attempts:

| code | meaning | qwen | codegemma | granite |
|---|---|---|---|---|
| OX0200 | unknown name | 274 | 280 | 456 |
| OX0203 | duplicate top-level name | 111 | 319 | 310 |
| OX0306 | receiver syntax on a non-builtin | 127 | 179 | 81 |
| OX0303 | not callable / wrong arity | 91 | 69 | 27 |
| OX0300 | operand type mismatch | 80 | 84 | 74 |

(EX0004, the explicit dialect's annotation-consistency check, adds 31
first-attempt occurrences for qwen — outside the five OX stage buckets,
as are all EX-codes.)

## Movement since the stale constrained probes

The pre-§53/§54 constrained probes (12 tasks, 24 generations, qwen 7B)
measured compile-clean 3/24 (12.5%) with 77 resolve occurrences. At
HEAD, qwen's constrained oxide first-compile is **30.0%** and first-pass
26.0%. Different task counts and seed depth make this directional
rather than a matched A/B, but the direction is consistent with §53/§54
having moved whole-program generation, not only repair.

## Unconstrained (the demand channel)

| family | oxide first-compile | explicit | rust first-pass |
|---|---|---|---|
| qwen | 10.0% | 8.5% | 56.5% |
| codegemma | 21.0% | 13.5% | 45.5% |
| granite | 4.0% | 3.0% | 42.0% |

The honest "can a small model write Oxide from a card" answer at HEAD
remains **no**: first-attempt lexer occurrences 2,163 (qwen), 1,952
(codegemma), 5,255 (granite) — the C-family-habit wall the 6a pilot
documented, unchanged in kind. Paired deltas are floor-bound
(codegemma's nominal +4.0pp SE 2.0 sits with both arms inside 10pp of
0% — §47 no-signal-at-floor; never quote it as a result). This
condition exists for the taxonomy's reached-for-syntax histograms, and
for this row, not for comparisons.

## Context exhaustion (new instrument semantics, measured)

Sessions whose repair prompt outgrew the window end with their
attempts-so-far recorded (`context_exhausted`; SPEC §45/§51's
evidence-gated rule, landed mid-campaign at `2e7dfc2`..`1cb59fa`). Both
conditions, by arm — `python -m eval.g0_report`, re-run against both
committed roots for this table (`--root .../constrained --run-prefix
g0c` and `--root .../unconstrained --run-prefix g0u`, `--seeds 1-10`):

| family | condition | exhausted cells | oxide | explicit | rust |
|---|---|---|---|---|---|
| qwen (8192) | constrained | 1/600 | 0 | 1 | 0 |
| qwen (8192) | unconstrained | 0/600 | 0 | 0 | 0 |
| codegemma (8192) | constrained | 1/600 | 1 | 0 | 0 |
| codegemma (8192) | unconstrained | 0/600 | 0 | 0 | 0 |
| granite (4096) | constrained | **100/600 (16.7%)** | 48 | 52 | 0 |
| granite (4096) | unconstrained | **41/600 (6.8%)** | 27 | 14 | 0 |

granite's rate is a direct consequence of its native window and is a
REAL RESULT about running a 4K-context model in a 4-attempt repair
loop — report it alongside any granite number, and treat granite's
cross-family comparisons as carrying this covariate on top of the
window itself.

**Unconstrained granite's arm split is asymmetric where constrained
granite's is not.** Constrained granite exhausts oxide and explicit at
close to arm-fair rates (48 vs. 52 of 600); unconstrained granite does
not (27 oxide vs. 14 explicit of 600) — the oxide arm loses
proportionally more of its repair budget to exhaustion than explicit
does, in the unconstrained condition specifically. Unconstrained
granite's first-compile (oxide 4.0% / explicit 3.0%) and final-pass
(oxide 7.5% / explicit 2.5%) rows above both carry this asymmetry as a
covariate: an exhausted session's `final_passed` is always `false` by
construction (exhaustion can only fire after a submitted, non-passing
attempt — see `eval.driver.run_session`), so the arm losing MORE
sessions to exhaustion has its own final-pass ceiling capped harder by
it, not inflated. Oxide loses more sessions to exhaustion here and
still shows the higher final-pass rate — if this covariate is doing
anything to that comparison, it is understating oxide's edge, not
producing it. Read the unconstrained granite row with that in mind
rather than pooling it with the 8192-pinned families' clean numbers,
and treat it as a covariate disclosure, not a corrected estimate — cell
counts this small (200 per arm, single-digit-percent rates) do not
support one.

## Limits

- **This baseline defines comparability going forward; nothing before
  it is a matched control.** The grammar gained bounded `//` comments
  and single-line structs (`73b84ae`/`98681ec`) and the backend moved
  from Ollama (pilot) to llama.cpp for both conditions, within this
  campaign. Pilot numbers are directional context only.
- **granite runs at n_ctx 4096** (its training context) — per-family
  window covariate plus the exhaustion rates above, in both conditions.
- **The context-exhaustion semantics changed mid-campaign** (abort →
  evidence-gated session result). Only qwen's constrained s6 was
  re-run across the boundary; the rule is symmetric across arms, and
  the 8192-pinned families' constrained runs hit it once each (zero
  unconstrained), so the practical footprint is granite, whose entire
  dataset ran on the final rule.
- **EX-codes are outside the stage buckets** — the explicit arm's own
  EX0001-5 diagnostics (31 first-attempt occurrences, qwen) appear in
  code-level counts but no stage row; a small undercount of the
  explicit arm's failure surface in stage terms.
- 0-shot only; q8_0 quants; one grammar; 20 tasks. The corpus was
  built for the whole-program instrument, not for exercising specific
  ergonomic constructs — corpus-level demand conclusions belong to the
  taxonomy, which reads the actual failing generations.

## What happens next

Per the design's loop: the taxonomy ranks the resolve/types wall's
concrete behaviors (unknown names, duplicate top-levels, non-builtin
receiver calls — what models actually wrote) into dossiers with
dose-response predictions, and the change loop starts at the top.

Raw: `constrained/`, `unconstrained/` (60 run dirs: cells, triples,
manifests, raw generations). Profiler: `python -m eval.g0_report`
(self-validating against the 6a pilot).
