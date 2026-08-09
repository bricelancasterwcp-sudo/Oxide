# v0.3 design — generation ergonomics (the measure-first loop)

**Date:** 2026-08-08
**Status:** approved design; implementation plan to follow.
**Prior:** the v0.3 gate decision (`2026-08-07-v03-gate-decision.md`)
rejected the ownership-default inversion and directed v0.3 at ergonomics,
naming the `OX0200` wall as the target. The data that grounded that
direction has since moved; this design re-grounds it.

## Why the target moved

The gate decision measured 63% name/type vs 35% linearity diagnostics.
That was pre-§53/§54. At HEAD, across all three families' post-`mut`
probe runs (320 failing oxide repairs):

- The pooled wall is 51% name/type vs 47% linearity — near parity, and
  the composition splits by family: codegemma's residue is almost pure
  linearity (98 of 107 failing rows carry `OX04xx`, mostly `OX0400`);
  granite's dominant failure is 91 compiled-but-wrong-output rows
  (model competence, not diagnostics); granite's famous `OX0200` wall is
  7 rows carrying ~16 occurrences each of Python-style implicit binding.
- Surface friction at the probe level is exhausted: failing submissions
  contain zero attempts at `+=`, `elif`, f-strings, or `&x`. Models have
  adapted to the card.
- The remaining probe failures happen with §40's clone-or-reorder
  suggestions verbatim in the prompt (verified). Repair-side teaching is
  spent; what remains there is reasoning, not ergonomics.

Meanwhile whole-program generation — the eval the language was built
for — has never reached the feature the language exists for: across
~530 Black Oxide-arm attempts in five 6a configurations, zero `OX04xx` ever
fired. Unconstrained (three configurations, ~480 attempts), models die
at the lexer/parser; the grammar-constrained follow-up moved the wall
only to resolve/types (77 `OX02xx` at 7B, compile-clean 3/24). Those
constrained probes predate §53/§54, so even that baseline is stale.

**v0.3 therefore targets whole-program generation.** Repair ergonomics
got §53/§54; generation ergonomics has never had its pass.

## Decisions (made 2026-08-08)

1. **Target: whole-program generation**, measured by the Part X session
   machinery, not the repair probe.
2. **Success criterion: the linearity gate populates** — or the wall is
   proven to be model competence. Exact exits in §Stopping below.
3. **Change size: sugar + demand-gated features.** Default lane is
   semantics-preserving sugar, card wording, and diagnostics (the
   §53/§54 template). SPEC-named v0.3 features (restricted closures,
   enum-scoped variants, module system) remain available but each needs
   (a) a demand dossier naming it specifically and (b) a SPEC extension
   committed before implementation.
4. **Method: measure-first loop** (approach A). Rejected: bundling all
   changes then measuring once (destroys attribution; builds from a
   demand table the pilot flags as pretraining-habit leakage), and a
   card-first track (3-shot already made 0.5B worse; card changes touch
   both Black Oxide arms at once and muddy the primary comparison). Card
   changes stay available as individual loop candidates.

## G0 — the baseline instrument

One grid, run under two decoding conditions, entirely on existing
machinery (`eval/driver.py` sessions, §45 verdicts, max 4 submissions):

- **Grid:** 3 local families (qwen2.5-coder-7b, codegemma-7b,
  granite-code-8b, all q8_0) × 3 arms × 20 tasks (`eval/tasks.jsonl`) ×
  **10 seeds**, 0-shot. Per-session checkpointing with resume.
- **Condition 1 — grammar-constrained (headline):** oxide/explicit under
  GBNF; rust free by design. The gate metric lives here.
- **Condition 2 — unconstrained (demand channel):** same grid. Under
  constraint the decoder deforms wants into legal tokens, so what models
  reach for is visible only unconstrained; it also refreshes the honest
  "can a small model write Black Oxide from a card" answer.
- **Collected:** first-attempt compile, first-attempt pass,
  attempts-to-green, per-code histogram by stage
  (lexer/parser/resolve/types/linearity), **`OX04xx` occurrence count
  (the gate metric)**, raw generations kept for forensics.
- **Primary comparison unchanged:** oxide vs explicit paired by task;
  rust is the capability control; §47 floor/ceiling no-signal guards
  apply.
- **Precondition — grammar parity:** the GBNF was generated at
  `3aa4e6d`; §54 is in it, §53 receiver syntax is unverified. Before any
  measurement, assert grammar-vs-parser parity at HEAD in both
  directions (constrained samples parse; the grammar admits every
  construct the card teaches). Parity becomes a standing test.
- **Cost:** estimated 4–8h GPU total; measured at G0 start. If
  egregious, the unconstrained condition may be trimmed to one family —
  it is a demand channel, not a headline.

G0 lands as `eval/results/g0-generation-baseline/` with a REPORT.md
stating its own limits, committed before any change is made.

## Taxonomy — the demand-discovery pass

After G0, before any change:

- Rank friction by measured frequency, per family and pooled — per-code
  histograms over failing sessions plus reading actual failing
  generations (a sample per top code, not just counts).
- Demand evidence has two legitimate sources: (a) unconstrained output —
  what models literally type; (b) deformation forensics under
  constraint — the §54 lesson, where gluing was the demand signal. A
  candidate needs support from at least one, quantified (N occurrences,
  M rows, which families).
- Each candidate gets a one-paragraph dossier: the friction, its
  frequency, the proposed fix, its class (sugar / card / diagnostic /
  SPEC-named feature), and the predicted effect — which code should
  fall, in which families — written **before** the measurement.
- Confound pinned: unconstrained demand is partly pretraining-habit
  leakage (§47). The dossier must argue the fix serves Black Oxide's design,
  not imitation; the anti-example is semicolon tolerance, tested and
  rejected in the 6a pilot (stripping semicolons surfaces the next
  C-family habit underneath).

## The change loop

One change at a time, the §53/§54 discipline:

1. Pick the top-ranked candidate. Sugar/card/diagnostic changes proceed
   directly; SPEC-named features need the dossier to name them and a
   SPEC extension committed first — the SPEC stays the binding contract.
2. Implement with tests (TDD, as all prior parts). When surface changes,
   the grammar updates in the same commit; parity is a standing
   invariant enforced by test.
3. Re-measure the affected condition — all three families, same seeds,
   before/after on matched code. Verify the prediction: the named code
   falls in the named families (dose-response), the rust arm stays flat
   (internal control; it reproduces byte-identically under fixed seeds),
   EX-side effects reported honestly.
4. Land as its own commit with a dated results dir + REPORT.md; the
   dossier's prediction and the outcome appear together. A fix that
   misses its target still lands only if independently justified (the
   `mut` precedent: instrument honesty); otherwise it is reverted. The
   decision is recorded either way.
5. Re-rank and repeat. Expected budget 3–5 loops; the stopping rule ends
   the track, not the budget.

## Stopping rule and the v0.3 ship

Two exits, both honest:

- **Gate populates:** `OX04xx` fires in constrained generation in at
  least 2 of 3 families, at pooled volume where the ownership-vs-explicit
  comparison has power — order of ≥30 occurrences, a **chosen**
  threshold, not derived. The Part X gate deliverable (the OX-code
  distribution for generated programs) becomes computable, and v0.3 has
  done its job.
- **Competence wall:** two consecutive loop iterations produce no
  candidate with a plausible dose-response prediction, or the taxonomy
  shows the residue is semantically-wrong-but-clean programs. v0.3 then
  ships the finding that generation friction is exhausted at this
  capability band — an honest null, as published before.

Ship ritual either way: synthesis REPORT with the full decomposition
(which change bought what, per family), README results section updated,
SPEC version bump v0.2.2 → v0.3, tag, memory update.

## Infra, error handling, testing

- One llama-server per family sequentially (16GB VRAM), Vulkan build,
  pinned `-c 8192`, preflight asserted, port ownership verified (the
  stale-server lesson), per-session checkpoint/resume — extend
  `driver.py`'s `is_complete`/`reset_run`, don't rewrite.
- Failure doctrine unchanged: `ModelError` = infrastructure, aborts
  loudly, never recorded as a model failure; truncation and rambles are
  results.
- Every stats script validates itself against already-published numbers
  before touching new data.
- Every language change TDD'd; full suite green before every commit.
- Layout: `eval/results/g0-generation-baseline/`, then one dated dir per
  change (the `method-syntax/` / `mut-fix/` pattern), each REPORT.md
  stating its own limits.

## Out of scope for v0.3

- Ownership-default semantics (settled by the gate decision).
- The small-model fine-tuning track (SPEC §32.4) — downstream of v0.3.
- Probe-side (repair) ergonomics — measured exhausted at HEAD.
- Any change to the probe corpus or repair instrument, except where a
  landed language change requires regenerating fixtures (handled as in
  §53/§54).
