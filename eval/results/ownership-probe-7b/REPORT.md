# Ownership Probe — first run (qwen2.5-coder 7B-instruct-q8_0)

**Date:** 2026-08-07
**Instrument:** `docs/superpowers/specs/2026-08-07-ownership-probe-design.md`
**Corpus:** `eval/probes.jsonl` — 6 defect classes × 3 arms, every record
mechanically verified (broken fails with the intended ownership code and
nothing else; fix compiles and reproduces the expected output).
**Design:** 18 probes × 3 seeds = 54 repairs per configuration.

## The headline

**This is the first time in the project's history that a model has been
observed engaging the linearity checker.** `OX0400` appears in the repaired
programs. Across the whole of Phase 6a — ~530 whole-program attempts in five
configurations — no `OX04xx` had ever fired.

## Results

Two configurations. In the second, the Oxide arms are grammar-constrained and
Rust is not; that asymmetry is justified below.

### Unconstrained

| Arm | strict | lenient | parsed |
|---|---|---|---|
| oxide | 4/18 | 4/18 | **4/18** |
| explicit | 2/18 | 7/18 | **7/18** |
| rust | **15/18** | 15/18 | **18/18** |

Oxide-arm diagnostics were `OX0001`×69, `OX0101`×36 — asked to *repair* a
program, the model emits unparseable Oxide. The syntax barrier follows into
the repair task, because a repair still requires emitting a whole program.

### Oxide arms grammar-constrained

| Arm | strict | lenient | parsed |
|---|---|---|---|
| oxide | **7/18** | 17/18 | 18/18 |
| explicit | **3/18** | 17/18 | 18/18 |
| rust | **15/18** | 15/18 | 18/18 |

**Why constraining only the Oxide arms is sound here:** Rust already parses
18/18 unaided. The grammar removes a confound the Rust arm does not carry
rather than granting the Oxide arms an advantage. This is a narrower claim
than "constrained-vs-unconstrained is fair in general", and it holds only
because the unconstrained Rust parse rate is 100% — check it again before
reusing this justification at another capability point.

## The primary comparison

Paired by defect class, strict score, Oxide − explicit-Oxide:

| Defect | oxide | explicit | diff |
|---|---|---|---|
| assign-to-iterated-vec | 0.0% | 0.0% | +0.0pp |
| double-consume | 0.0% | 0.0% | +0.0pp |
| loop-carried-move | 33.3% | 66.7% | **−33.3pp** |
| move-inside-branch | 66.7% | 0.0% | +66.7pp |
| move-then-read-field | 66.7% | 0.0% | +66.7pp |
| use-after-move | 66.7% | 33.3% | +33.3pp |

**Paired delta +22.2pp, paired SE 16.5pp, n = 6 defect classes.**
2-SE interval `[−10.7, +55.2]pp`.

Clears the pre-registered ±5pp band. **Does not clear 2 SE.** The point
estimate favours implicit linearity by a wide margin and one class runs the
other way, but six defect classes cannot resolve it. This is *suggestive and
unresolved*, and it must not be reported as support — the band was calibrated
for a 20-task corpus, and this corpus has six.

The obvious next step is more defect classes, not more seeds: the interval is
driven by between-class variance, so seeds 4–10 would barely move it.

## The finding that matters most

**The degenerate-fix rate.**

| Arm | lenient | strict | ownership cleared, behaviour changed |
|---|---|---|---|
| oxide | 17/18 | 7/18 | **10/18** |
| explicit | 17/18 | 3/18 | **14/18** |
| rust | 15/18 | 15/18 | **0/18** |

Both Oxide arms clear the ownership diagnostic on 17 of 18 repairs — and more
than half of those "repairs" change what the program does. Rust does this
**zero** times.

The models make the ownership error go away without understanding what the
program was for. They delete the second use, or drop the value early, or
restructure until the checker is quiet.

This vindicates the strict-scoring decision completely. On the lenient score
alone the ranking reads **oxide 17 ≈ explicit 17 > rust 15** — Oxide arms
beating Rust. The strict score reverses it to **rust 15 > oxide 7 > explicit
3**. Anyone reporting lenient without strict would have drawn exactly the
wrong conclusion, and the lenient metric as originally specified was also
gameable by empty output (fixed in `bc569d6`).

## Secondary observations

- `assign-to-iterated-vec` is 0/3 in **all three arms** — universally the
  hardest defect, and the only one Rust fails.
- `double-consume` is 3/3 in Rust and 0/3 in both Oxide arms.
- Rust's only failures are `E0502`, the same class.
- Rust's advantage (15/18 vs 7/18) remains a pretraining-exposure measurement,
  not evidence about language design, per the standing rule.

## What this run does and does not establish

**Does:** the instrument works. It reaches linearity, it produces a
per-defect distribution, and it separates genuine repair from
diagnostic-silencing. Phase 6a's whole-program eval could do none of these.

**Does not:** settle the thesis. +22.2 ± 16.5pp over six classes is not a
result. It is a reason to build the corpus out.

## Reproduce

```bash
~/llama.cpp/build-vk/bin/llama-server -m <qwen2.5-coder-7b-q8_0.gguf> \
    --port 8081 -ngl 99 -c 8192
```
Raw per-repair records: `unconstrained.json`, `constrained.json`.
