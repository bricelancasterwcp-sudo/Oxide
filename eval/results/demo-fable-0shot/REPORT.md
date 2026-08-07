# Oxide Three-Way Eval — Run `demo-fable-0shot` (2026-08-07)

**Design:** 20 language-neutral tasks × 3 arms (Oxide, explicit-Oxide, Rust),
0-shot, 4-attempt repair cap, harness as sole oracle. Subject: Claude Fable 5
via isolated workflow agents under a no-repo-access protocol; Oxide/explicit
arms taught by their language cards, Rust arm by a one-line preamble with
`rustc --error-format=json` (help text verbatim) as its diagnostics.

## Results

| Metric | Oxide | Explicit-Oxide | Rust |
|---|---|---|---|
| First-attempt compile | 20/20 | 20/20 | 20/20 |
| First-attempt pass | 20/20 | 20/20 | 20/20 |
| Final pass | 20/20 | 20/20 | 20/20 |
| Mean attempts-to-pass | 1.00 | 1.00 | 1.00 |
| Diagnostics triggered | none | none | none |
| Mean tokens / cell | 39.2k | 40.1k (1.02×) | 36.5k (0.93×) |
| Mean wall-clock / cell | 29.9s | 37.0s | 19.7s |

## Findings

1. **Ceiling effect — the headline.** Every arm saturated. At frontier
   capability and this task difficulty, the eval does not discriminate
   between implicit linearity, explicit linearity, and Rust. No repair
   loops fired; the per-OX-code error distribution the v0.3 gate needs is
   EMPTY at this operating point.
2. **The language card works.** A never-before-trained language was written
   correctly 20/20 first-try from an ~850-word in-context card, including
   enums/match, linear accumulation idioms, `?`, and string processing.
   In-context teachability at frontier scale is demonstrated.
3. **Explicit annotations were not a frontier-scale burden.** The control
   arm also went 20/20 — hand-written `&`-reads, param modes, and `drop`
   placement did not induce errors. It cost +2% tokens and +24% wall-clock
   vs implicit. At this scale, implicitness bought convenience, not
   correctness.
4. **The novel-language tax is visible and small.** Rust ran 7% cheaper and
   34% faster — roughly the language-card overhead paid per request plus
   less deliberation. With no correctness delta, Rust wins this operating
   point on cost. The thesis must find its value where correctness deltas
   exist.

## Caveats

- Subject is the same model family that designed the language and authored
  its card; blindness was instruction-level, not infrastructure-level.
- One capable subject, one shot condition, 20 tasks: no variance estimates.
- These numbers demonstrate the instrument; they neither validate nor
  refute the thesis.

## What creates discrimination next

1. **Drop subject capability** — the adopted small-model track (Qwen-Coder
   1.5B/7B, compiler-filtered fine-tuning corpus, pass@N with `--check`
   verifier). This is where the ownership-bookkeeping-in-the-compiler
   hypothesis predicts a real gap.
2. **Raise task difficulty** — a `hard2` tier (multi-function programs,
   aliasing-heavy data flows, resource-shaped state machines) targeting
   the error classes OX04xx exists to catch.
3. **Measure at the margin** — tokens/latency now separate arms even when
   accuracy cannot; report both in all future runs.

Raw per-cell data: `cells.json` (all 60 submitted programs verbatim).
