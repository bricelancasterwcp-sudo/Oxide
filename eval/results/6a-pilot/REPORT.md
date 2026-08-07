# Phase 6a Pilot — the ladder sits below the phenomenon

**Date:** 2026-08-07
**Status:** pilot, not a reported grid result. Written to `eval/results/6a-pilot/`
from a scratch run root, deliberately outside the grid namespace.

## Why this run exists

The Phase 6a design (SPEC Part X) specifies a 1800-session grid: 3 models ×
3 arms × 20 tasks × 5 seeds × 2 shot conditions, ~2–4h. Before committing to
it, three single-`run_id` probes were run as a pipeline rehearsal. They were
never intended to be analysed — but the diagnostics they produced answer the
question the grid was meant to answer, and answer it against running the grid
as specified.

## Results

Three probes, 180 sessions, ~660 generations.

| Probe | oxide 1st-compile | explicit 1st-compile | rust 1st-compile | rust final-pass |
|---|---|---|---|---|
| 0.5B 0-shot | 0/20 | 0/20 | 12/20 | 8/20 |
| 0.5B 3-shot | 0/20 | 1/20 | 8/20 | 1/20 |
| 7B 0-shot | 2/20 | 0/20 | **20/20** | 12/20 |

### The finding

Across ~480 Oxide-arm attempts producing ~5,000 diagnostics: **zero `OX04xx`
linearity codes.** Not one.

| Layer | 0.5B 0-shot | 0.5B 3-shot | 7B 0-shot |
|---|---|---|---|
| `OX0001` lexer | 1462 | 810 | 915 |
| `OX01xx` parser | 658 | 551 | 629 |
| `OX02xx`/`OX03xx` semantic | 3 | 11 | 4 |
| **`OX04xx` linearity** | **0** | **0** | **0** |

Every Oxide failure is lexical or syntactic. The models never reach the
semantic layer, so the eval at these capability points measures **syntax
acquisition, not ownership reasoning** — and the ladder's top rung is no
exception. 7B compiles Rust 20/20 and Oxide 2/20.

### What the models actually emit

Characters Oxide's lexer rejects, counted across both Oxide arms:

| Character | Count | What the model was reaching for |
|---|---|---|
| `;` | 1575 | statement terminators |
| `[` `]` | 192 each | indexing / array literals |
| `'` | 160 | char literals or lifetimes |
| `\|` | 122 | closures |
| `&` | 112 | references |
| `#` | 22 | attributes |

A representative 7B attempt at t01, which is *nearly* right:

```
fn sum_of_squares() -> Int {
    let sum = 0
    for i in range(0, 10) {
        sum += i * i        // Oxide has no compound assignment
    }
    sum                     // and no implicit tail return here
}
```

These are not confused outputs. They are competent Rust/Python habits leaking
into a language whose grammar is narrower — a pretraining-exposure artifact,
exactly the confound SPEC §47 says must not be read as evidence about language
design.

### The cheap fix does not work

Semicolons are the most *frequent* rejection, so the obvious intervention is to
tolerate or strip them. Tested by re-running every stored first-attempt output
with trailing semicolons removed — no new generations required:

| Probe | arm | as-is | semicolons stripped |
|---|---|---|---|
| 7B 0-shot | oxide | 2/20 | 2/20 |
| 7B 0-shot | explicit | 0/20 | 1/20 |
| 0.5B 0-shot | oxide | 0/20 | 0/20 |
| 0.5B 3-shot | explicit | 1/20 | 2/20 |

Removing semicolons surfaces the next syntax error underneath. The barrier is a
*stack* of C-family habits, not a single one, which is why per-character lexer
tolerance is not the answer and grammar constraint is.

## A hazard this exposed in the pre-registered analysis

At 7B 0-shot the paired-by-task delta is 2/20 − 0/20 = **+10.0pp**, which clears
SPEC §47's `≥ +5pp` band. The report printed:

```
| qwen7b | 0 | +10.0 | 6.9 | supports | 10% | 0% | 60% |
```

"supports" — off two programs, with a paired SE of 6.9pp that does not
distinguish the delta from zero. The ±5pp band was derived from a power
calculation at p≈0.5; at p≈0.05 that derivation does not hold.

Fixed in `cb250ab`: a point where both primary arms sit within 10pp of 0% or of
100% now reports `no-signal-at-floor` / `no-signal-at-ceiling` and carries no
pre-registered reading. The delta and SE are still printed. Neither the band nor
either direction changed — this honours §47's stated limits rather than
renegotiating its conclusions, and it was derived from pilot data written
outside `eval/results/` before any grid run.

## Operational findings

- **Runtime is far below budget.** 60 sessions in 1m49s at 0.5B; 220
  generations, 1 truncation, mean 178 output tokens. Model time was 104s of
  109s wall — rustc is nearly free because the Oxide arms fail in the
  transpiler and never reach it. The full grid would be ~2–4h, not 8–14h.
- **`contract_compliant` is 0/60 across every arm.** Instruct models wrap output
  in markdown fences without exception. Extraction handles it correctly and the
  metric is formatting-only by design, but that column will read 0% throughout.
- **The pipeline works end to end on real data** — sessions, triples, cells,
  manifest, rollup.

## Recommendation

**Do not run the grid as specified.** It would spend 2–4h to produce three rungs
of near-zero Oxide performance and an empty `OX04xx` histogram — run 1's
empty-gate failure, arriving from the floor instead of the ceiling.

The binding constraint is syntax, and the way to remove it is
**grammar-constrained decoding**: with a GBNF grammar the model can only emit
parseable Oxide, every remaining failure is semantic, and the gate deliverable
finally populates. This isolates the actual question — *given valid syntax, does
implicit linearity help?* — which is what the language exists to answer.

Two caveats that belong in the analysis plan up front:

1. Constrained decoding changes what "writes the language" means. The
   unconstrained result above is the honest answer to "can a small model write
   Oxide from a card" — **no** — and should be reported as such rather than
   replaced.
2. Ollama silently ignores a `grammar` option (verified: it returns free text),
   so this requires `llama.cpp`. The `ModelClient` protocol was built for that
   swap.

## Provenance

Raw data under `eval/results/6a-pilot/`: three `run_id` directories, 653 raw
model outputs verbatim, `cells.jsonl`, and the `triples.jsonl` carrying every
diagnostic counted above. Models: `qwen2.5-coder` instruct at uniform `q8_0`,
digests in each run's `manifest.json`.
