# Ownership Probe — 20 classes (qwen2.5-coder 7B-instruct-q8_0)

**Date:** 2026-08-07
**Corpus:** `eval/probes.jsonl` — 20 defect classes × 3 arms, 60 records, every
one mechanically verified (broken fails with its intended ownership code and
nothing else; fix compiles and reproduces its expected stdout; all three arms
of a class agree on that stdout).
**Design:** 20 × 3 arms × 3 seeds = 180 repairs. Oxide arms
grammar-constrained; Rust unconstrained (it parses 60/60 unaided, so the
grammar removes a confound Rust does not carry rather than granting an
advantage).

## Result

| Arm | strict | lenient | parsed | degenerate |
|---|---|---|---|---|
| oxide | **20/60** | 52/60 | 58/60 | 32/60 |
| explicit | **9/60** | 49/60 | 60/60 | 40/60 |
| rust | **53/60** | 54/60 | 60/60 | 1/60 |

### The primary comparison is now resolved

Paired by defect class, strict score, Oxide − explicit-Oxide:

**+18.3pp, paired SE 7.0pp, n = 20 classes. 2-SE interval `[+4.3, +32.4]`.**

The interval excludes zero. It clears the pre-registered ±5pp band **and**
2 SE — the first statistically resolved result in this project's history.

Growing the corpus is what did it. The point estimate barely moved; the
interval more than halved:

| | delta | SE | 2-SE interval | resolved |
|---|---|---|---|---|
| 6 classes | +22.2pp | 16.5pp | [−10.7, +55.2] | no |
| **20 classes** | **+18.3pp** | **7.0pp** | **[+4.3, +32.4]** | **yes** |

Two robustness checks, both agreeing:

- **Sign test**, ties excluded: 8 of 9 non-tied classes favour Oxide,
  two-sided exact binomial **p = 0.039**. Non-parametric, so it does not
  lean on the normality the SE assumes.
- **Dropping the 11 classes where both arms score zero**: n = 12,
  **+30.6 ± 10.4pp**, interval `[+9.7, +51.4]`. The effect survives, larger,
  when the mutual-failure classes are removed.

### Per-class

| Defect | oxide | explicit | rust | diff |
|---|---|---|---|---|
| move-into-vec-push | 100.0 | 0.0 | 100.0 | **+100.0** |
| move-then-early-return | 66.7 | 0.0 | 100.0 | **+66.7** |
| move-then-read-field | 66.7 | 0.0 | 100.0 | **+66.7** |
| double-move-in-one-expression | 33.3 | 0.0 | 100.0 | +33.3 |
| move-in-match-scrutinee | 33.3 | 0.0 | 66.7 | +33.3 |
| move-in-nested-block | 66.7 | 33.3 | 100.0 | +33.3 |
| move-inside-branch | 66.7 | 33.3 | 100.0 | +33.3 |
| use-after-move | 66.7 | 33.3 | 100.0 | +33.3 |
| loop-carried-move | 33.3 | 66.7 | 100.0 | **−33.3** |
| *11 further classes* | | | | 0.0 |

`loop-carried-move` is the sole class favouring explicit-Oxide, and it is the
one where writing `drop` out plausibly helps: the fix is to rebind inside the
loop, and the explicit arm's `drop` placement makes the iteration boundary
visible. Worth a closer look rather than dismissal.

Four classes defeat every arm including Rust: `assign-to-iterated-vec` is
0/0/0, and `move-via-question-mark` and `move-via-struct-update` leave Rust at
33% and 67%.

## The degenerate-fix rate is the other headline

| Arm | ownership silenced, behaviour changed |
|---|---|
| oxide | 32/60 |
| explicit | 40/60 |
| rust | **1/60** |

Both Oxide arms clear the diagnostic far more often than they repair the
program. Rust does this essentially never.

On the lenient score alone the ranking reads **oxide 52 ≈ explicit 49 ≈
rust 54** — all three indistinguishable. The strict score separates them
completely: **rust 53 ≫ oxide 20 > explicit 9**. Reporting lenient without
strict would have erased the entire result.

## What this establishes, and what it does not

**Establishes:** given a program with one ownership defect and the compiler's
explanation, this model repairs implicit-linearity Oxide more reliably than
explicit-linearity Oxide, at matched novelty, by 18.3pp (2-SE `[+4.3, +32.4]`,
sign-test p = 0.039). That is the thesis, tested directly, and supported.

**Does not establish:**

1. **One model, one shot condition, three seeds.** No claim about capability
   scaling. The 6→20-class jump moved the delta by 4pp, so the estimate looks
   stable, but a second model is the obvious next control.
2. **Repair, not authorship.** Phase 6a established that this model cannot
   *write* Oxide at all (2/20 first-compile). This probe supplies everything
   except the ownership decision. Both results are true and neither replaces
   the other.
3. **Oxide arms are grammar-constrained.** Justified here by Rust's 60/60
   unaided parse rate; that justification must be rechecked at any other
   capability point.
4. **Rust's 53/60 remains a pretraining-exposure measurement**, not evidence
   about language design, per the standing rule.
5. **11 of 20 classes are tied**, most at 0/0. The effect rests on 9 classes.
   More classes would tighten it further; classes that defeat both arms
   contribute nothing but cost nothing.

## Reproduce

```bash
~/llama.cpp/build-vk/bin/llama-server -m <qwen2.5-coder-7b-q8_0.gguf> \
    --port 8081 -ngl 99 -c 8192
```
Raw per-repair records: `results.json`.
