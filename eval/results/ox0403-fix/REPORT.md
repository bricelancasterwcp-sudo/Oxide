# OX0403 diagnostic fix — did it change model behaviour?

**Date:** 2026-08-07
**Change:** `a9f336c`. `OX0403` now attaches a `later used here` note pointing
at the use that makes the move fatal, and its suggestion no longer offers
"or clone it" as a co-equal option.

The defect was found by the ownership probe: in a loop the move site and the
conflicting use are the same syntax, so the diagnostic named one location
twice and never mentioned the post-loop read. rustc names both ends.

## Frontier: fail → pass, both Oxide arms

Same probe, same subject, same protocol. The only thing that changed is the
diagnostic text the model was given.

| | before | after |
|---|---|---|
| oxide | `let grown = push(clone(acc), i)` → `10, 10` — **fail** | `acc = push(acc, i)` → `10, 13` — **pass** |
| explicit | same clone route — **fail** | **pass** |

The pre-fix behaviour was observed twice, in two independent runs (the
original frontier sweep and the post-p17-rewrite retest), in both dialects.
The post-fix behaviour is the correct accumulating repair in both. This is
the clean result: the model was following the compiler's advice, the advice
was wrong, and correcting it corrected the model.

## 7B: within noise, and not claimed as improvement

Three loop-carried classes, 3 seeds per cell, strict score:

| class | oxide before → after | explicit before → after |
|---|---|---|
| accumulate-without-reassign | 0 → **33** | 0 → 0 |
| loop-carried-move | 33 → **0** | 67 → 33 |
| move-in-while-body | 67 → **33** | 67 → 67 |

Movement in both directions, one or two programs per cell. **No claim of a
7B improvement is supported by this.** A real measurement needs the full
20-class corpus at more seeds, which is a larger run than this one.

The honest summary: the fix is demonstrably causal at frontier and unmeasured
at 7B.

## Why this matters beyond one probe

This is the first time the eval was used to *improve* the language rather than
judge it, and the loop closed: probe found the defect → diagnostic fixed →
probe confirms the fix landed. p17 was retained specifically as that
regression target, and it flipped.

It also qualifies an earlier finding. The Oxide-vs-Rust gap was attributed to
pretraining exposure; this shows part of it was diagnostic quality, which is
fixable. How much of the remaining gap is fixable the same way is now an open,
testable question rather than an assumption.

Raw: `7b-loop-classes.json`.
