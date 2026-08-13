# Most of the "language design" win was ergonomics: a decomposition under matched novelty

*Black Oxide findings series — 2026-08-12. All numbers are reproducible
from this repository; sources are linked inline.*

## The claim

Small models repair implicit linear types far better than explicit ones
— up to **+59pp** — but when the effect is decomposed, only about
**+10pp is ownership**. The rest is surface ergonomics, dominated by one
change: letting method syntax compose. And at frontier capability the
whole effect vanishes: **0.0pp**, arms identical. If you are designing a
language, DSL, or schema that small models must repair, the actionable
finding is not "make ownership implicit" — it is "meet the pretraining
prior, because ergonomic friction dwarfs semantic design".

## The method: matched novelty

Comparing a new language against Rust confounds design with pretraining
exposure. This project's control removes that: **Black Oxide vs explicit
Black Oxide**, two dialects with identical grammar, builtins, and
diagnostics — differing in exactly one thing, whether ownership is
implicit (inferred moves, borrows, and drops) or written out by hand
(`&` reads, declared parameter modes, mandatory `drop`). Both are
novel to every subject, taught only by compiler-validated language
cards of matched length (895 vs 980 words). Any delta between the arms
is therefore about the design variable, familiarity-free.

The task is **repair, not generation**: a correct program carrying one
seeded ownership defect, plus the compiler's diagnostic; the model
proposes a fix; `rustc` (via transpilation) is the oracle. 20 defect
classes × 3 arms × 10 seeds = 600 repairs per family.

## The headline, and its ceiling

| Subject | Black Oxide | explicit | paired delta | 2-SE |
|---|---|---|---|---|
| Claude Opus 5 † | 92% | 92% | **0.0pp** | ceiling, arms identical |
| qwen2.5-coder-7b | 73.0% | 14.0% | **+59.0pp** | `[+42.8, +75.2]` |
| codegemma-7b | 46.5% | 11.5% | **+35.0pp** | `[+19.7, +50.3]` |
| granite-code-8b | 20.5% | 11.0% | **+9.5pp** | `[+3.8, +15.2]` |

Pooled across families: p < 10⁻⁹; combined **+34.5pp `[+25.3, +43.7]`**.
† The frontier row is a different instrument (12 hardest classes × 1
seed, unconstrained on all arms; 11/12 and 11/12) and establishes one
thing only: the delta vanishes at ceiling. A fifth subject
(DeepSeek-Coder-V2-Lite 16B) returned INCONCLUSIVE under its own
pre-registration — the effect lives in a **capability window**: strong
enough to attempt the task, weak enough to be helped. Full method and
per-family data: [README](../../README.md), `eval/results/`.

## The decomposition

Adding builtin method syntax — `v.clone()` for `clone(v)`, sugar only,
no semantics change ([SPEC §53](../../SPEC.md)) — moved the numbers
enormously, and *the way it moved them* is the finding:

| Model | `OX0304` before → after | Black Oxide strict change |
|---|---|---|
| qwen | 94 → **0** | **+42.0pp** |
| codegemma | 72 → **0** | **+31.0pp** |
| granite | 9 → 12 | **−1.5pp** |

82% of failing repairs had contained `.clone()` method syntax — Rust's
near-universal receiver-first convention colliding with a prefix-only
builtin surface. The fix helped exactly the families that had the
problem and did nothing for the one that didn't (granite never wrote
method syntax): dose-response across independent families.

The decomposition follows:

- **Ownership alone ≈ +10pp.** granite — untouched by every ergonomic
  fix — sits at +9.5pp `[+3.8, +15.2]`. That is the clean estimate of
  what implicit linearity itself buys.
- **Ergonomics: larger and family-dependent, up to +42pp.** Method
  syntax composes with implicit ownership (`v.len()`) and awkwardly
  with explicit ownership (`(&v).len()` — the receiver still needs its
  marker), so ergonomic relief and the implicit arm interact. That
  interaction, not ownership semantics, carries most of the headline.
- The combined +34.5pp **should not be quoted as an ownership result.**

The same dose-response form recurs down the stack: accept-and-discard
`mut` (SPEC §54) was worth +5.5/+3.0/0pp along the same family lines,
and in the generation campaigns the `vec(...)` literal (§55) accounts
for 87% of the first-compile gain with its per-family effect ordered by
each family's own demand counts (91/69/27 `vec` calls →
+4.5/+3.5/+2.0pp).

## The counter-result that disciplines the story

Models **cannot write** Black Oxide from a card: constrained first-pass
generation scored 30.5/16.5/9.5% against 56.5/45/42% for Rust — and a
frontier model scored 20/20 on all three arms. The generation gap is
pretraining exposure, not language design, and no card fixes it. The
repair loop is where a novel language can win; whole-program authorship
is where the pretraining prior is unbeatable. (This is also why the
project's eval instrument is repair: ~530 whole-program generation
attempts never once reached a linearity diagnostic — small models fail
at parse and name resolution first, so generation cannot even measure
the design variable.)

Consistent with that, the project's own v0.3 gate **rejected** inverting
the ownership default (docs/superpowers/specs/2026-08-07-v03-gate-decision.md):
the isolated ownership benefit is ~+10pp and 0.0pp at frontier, while
the largest measured win in the whole project came from ergonomics at
zero semantic cost.

## Design guidance, if you are building a language or DSL for model repair

Everything below is a measured result in this repository, not taste:

1. **Repair beats generation.** Give the model a correct artifact, one
   defect, and a precise diagnostic. Do not ask a small model to author
   whole programs in your novel surface.
2. **Meet the pretraining prior.** Receiver-first method calls,
   assignment to fields, `mut` tolerated — every fight you pick with
   reflexive habits costs measurable points (up to +42pp for one piece
   of sugar). A sibling project's law states it as: do not invent a
   DSL — 2/20 vs 20/20.
3. **Implicit beats explicit markers, modestly.** ~+10pp for inferring
   ownership rather than requiring annotations — real, but a fraction
   of the ergonomic effects, and zero once the model is strong enough.
4. **Fail closed, with specific diagnostics.** The entire loop depends
   on the compiler objecting precisely. (The one defect class found
   that escapes the oracle — a clone-then-mutate the transpiler's
   target accepts — is documented, not hidden.)
5. **Expect the effect to be a window.** Too weak and the model cannot
   attempt the task; too strong and it no longer needs the help. Size
   your design effort for the models that live inside the window.

## Honest limits

- One eval corpus (20 tasks), one seeded-defect distribution, three 7–8B
  families at q8_0 plus one frontier subject; the capability-window
  subject was inconclusive by pre-registration.
- The local arms were measured under grammar constraint; the companion
  finding ([constrained decoding deforms rather than
  rejects](2026-08-12-constrained-decoding-deforms.md)) quantifies the
  artifact class that introduces, and the frontier row was run
  unconstrained on all arms for exactly that reason.
- Whether the repair advantage survives once pretraining exposure is
  equalised is open — the next experiment is a token-matched fine-tune
  of Black Oxide against Rust (SPEC §32.4).
- Three earlier headline claims were withdrawn when they failed to
  replicate (README, "Why earlier estimates were lower"); the numbers
  here post-date and survive those corrections.
