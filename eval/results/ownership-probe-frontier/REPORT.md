# Ownership Probe — frontier subject

**Date:** 2026-08-07
**Subject:** Claude Opus 5 via isolated agents, one per (class, arm), each given
a single self-contained prompt file outside the repository and forbidden to
read, search, or list anything else. Blindness is structural: the agents had no
path to the corpus, the reference fix, or the expected output. Prompts were
leak-checked before dispatch (no `expected_stdout`, no `fix`).
**Design:** 12 defect classes × 3 arms × 1 seed = 36 repairs. No grammar
constraint in any arm — the frontier subject does not need it.

## Result: ceiling

| Arm | strict | lenient |
|---|---|---|
| oxide | **11/12** | 12/12 |
| explicit | **11/12** | 12/12 |
| rust | **12/12** | 12/12 |

**Paired delta, Oxide − explicit-Oxide: 0.0pp.** The two arms are identical
class for class.

## The effect is capability-dependent

| Class | frontier ox/ex/ru | 7B ox/ex/ru |
|---|---|---|
| use-after-move | 1/1/1 | 67/33/100 |
| double-consume | 1/1/1 | 0/0/100 |
| loop-carried-move | 1/1/1 | 33/67/100 |
| assign-to-iterated-vec | 1/1/1 | **0/0/0** |
| move-then-read-field | 1/1/1 | 67/0/100 |
| move-inside-branch | 1/1/1 | 67/33/100 |
| move-into-struct-literal | 1/1/1 | 0/0/100 |
| move-into-enum-variant | 1/1/1 | 33/33/100 |
| move-via-struct-update | 1/1/1 | 0/0/67 |
| move-via-question-mark | 1/1/1 | 0/0/33 |
| accumulate-without-reassign | **0/0/1** | 0/0/100 |
| consume-while-iterating | 1/1/1 | 0/0/100 |

The classes selected for this run were deliberately the *hardest* ones at 7B —
including `assign-to-iterated-vec`, which defeated every arm at 7B (0/0/0), and
`move-via-question-mark`, where even Rust managed only 33%. The frontier
subject cleared all of them.

**This narrows the thesis, and the narrowing is the finding.**

- At 7B: **+18.3pp** favouring implicit linearity, 2-SE `[+4.3, +32.4]`,
  sign-test p = 0.039.
- At frontier: **0.0pp**. Both arms at ceiling.

Implicit linearity helps models that are not already good at ownership. It
buys nothing for a model that already reasons about ownership correctly. The
honest claim is therefore **"implicit linearity is an accessibility win for
weaker models"**, not "implicit linearity makes LLMs more reliable" — which is
a materially more modest thesis than the project set out with.

This is consistent with, and explains, run 1's ceiling: at frontier scale the
three arms scored 20/20 on whole-program authorship too.

## The single failure is a corpus defect, not a model failure

`accumulate-without-reassign` (p17) failed in **both** Oxide arms, identically.
The subject wrote:

```
let grown = push(clone(acc), i)     # instead of:  acc = push(acc, i)
print(len(grown))
```

That compiles and clears `OX0403` but does not accumulate, so it strict-fails.
It is also **what the compiler's own diagnostic suggests** — `OX0403`'s
suggestion text ends "…or clone it", and rustc emits `help: consider cloning`
for the matching program.

The class has two valid readings of "fix this", with different observable
behaviour, and the probe cannot distinguish a model that misunderstood from a
model that took the compiler's advice. The authoring agent flagged this when
building the class; this run confirms it empirically.

It affects both Oxide arms identically, so it does not bias the primary
comparison. But p17 should be rewritten so that only the accumulating repair
compiles, or retired.

## Method notes

- 36 isolated agents, one per cell, ~10–35s each. Independence between probes
  was preserved deliberately: batching probes into one agent would let earlier
  answers inform later ones, which pushes toward ceiling — the very hypothesis
  under test — and is therefore the non-conservative direction.
- Eight classes were run first (p01–p08); the result was already 23/23. The
  remaining four were chosen as the hardest-for-Rust classes at 7B, on the
  reasoning that a ceiling breaks there if it breaks anywhere. It did not.
  The other eight classes were not run, on the grounds that confirming a
  ceiling already at 23/23 with easier classes has near-zero information value.
  That is a deliberate stopping rule, stated here rather than presented as a
  complete sweep.

## Raw data

`results.json` — per-cell scores. `answers/` — every submitted repair verbatim.

---

## Addendum — the p17 rewrite failed, and why that matters more

p17 was rewritten (`651dfff`) to remove the accumulate-vs-clone ambiguity: the
print moved outside the loop so a clone-based repair could no longer be
satisfied locally, and only reassignment produces `10` then `13`.

**The frontier subject clones anyway.** Re-tested against the rewritten probe:

```
let grown = push(clone(acc), i)      → prints 10, 10   (expected 10, 13)
```

Both Oxide arms, strict-fail. Rust: correct. The rewrite made the error
visible to a *reader* — `grown` is now conspicuously unused — without changing
model behaviour at all.

### The actual cause is diagnostic richness, not probe ambiguity

Both languages' diagnostics recommend cloning. rustc's is *more* explicit,
printing the exact edit:

```
help: consider cloning the value if the performance cost is acceptable
10 |         let grown = push_back(acc.clone(), i);
```

The model ignored that advice for Rust and took it for Oxide. The difference
is everything else in the message:

| | Oxide `OX0403` | rustc `E0382` |
|---|---|---|
| move site | line 5 col 26 | line 10, `value moved here, in previous iteration of loop` |
| **the later use that makes it fatal** | **absent** | **line 12, `value borrowed here after move`** |
| loop context | absent | `inside of this loop` |
| why the type moves | absent | `does not implement the Copy trait` |
| `note` | repeats the error position | three distinct sites |

rustc shows **both ends of the conflict**. Oxide shows only the move. A model
that cannot see that `acc` is read after the loop has no way to know cloning
inside the loop defeats the purpose — so it follows the suggestion literally.

**This is an actionable defect in Oxide's diagnostics, found by the probe.**
`OX0403`'s note points at the same position as the error, which carries no
information. It should point at the *later use*, as `OX0400`'s note already
does for the non-loop case ("includes a note pointing at the earlier move").
The suggestion should also not offer "or clone it" as a co-equal option in the
accumulator case, where cloning silently breaks the program.

### Scope of the confound

SPEC §45 gives the Rust arm rustc's full diagnostic text deliberately — it is
part of the null hypothesis. But this shows Oxide-vs-Rust comparisons are
partly measuring *diagnostic quality*, not language design, and the gap is
larger than "Rust has more pretraining exposure" alone accounts for.

**The primary comparison is unaffected.** Oxide and explicit-Oxide receive the
identical `OX0403` diagnostic, so this cannot bias the +18.3pp result. It bears
on the Rust reference column only — which the standing rule already says is not
evidence about language design.

### Corpus status

p17 is retained in its rewritten form. It is a *sound* probe — one intended
code, verified fix, unambiguous to a reader — that both Oxide arms fail for a
reason now understood. That makes it a useful regression target for any future
improvement to `OX0403`: if the diagnostic is fixed to name the later use, this
probe should start passing.
