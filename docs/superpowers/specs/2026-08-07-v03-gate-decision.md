# v0.3 gate decision — the ownership-default inversion is REJECTED

**Date:** 2026-08-07
**Gate:** SPEC Part VI §32.3 proposed inverting the ownership default — value
semantics for plain data (implicit clone at would-be use-after-move), with
linearity opt-in per type (`resource struct`) and `OX04xx` applied only there.
It was recorded as **eval-gated: do not implement yet**, to be confirmed or
killed by the eval's OX-code error distribution.

**Decision: do not invert. Linearity stays the default.**

## The evidence the gate asked for

OX-code distribution across 334 failing Oxide repairs (three model families,
10 seeds, 464 diagnostics):

| Layer | count | share |
|---|---|---|
| syntax `OX0001`/`OX01xx` | 8 | 1.7% |
| names & types `OX02xx`/`OX03xx` | 293 | 63.1% |
| **linearity `OX04xx`** | **163** | **35.1%** |

Linearity codes: `OX0400` 100, `OX0401` 24, `OX0403` 24, `OX0406` 15.

So the inversion would remove roughly a third of the diagnostics models
actually hit. That is a real ergonomic win and it is the strongest argument
in favour.

## Why it is still rejected

**1. The "cloning is the right fix" evidence is an artifact of the corpus.**
19 of 20 probe classes have a reference fix that clones, which looks decisive
until you check whether the defect *requires* it. It does not. For
`use-after-move`, simply reordering the two statements compiles and passes:

```
let a = ...            let a = ...
let b = a              print(len(a))     # read first
print(len(a))   ->     let b = a         # then move
print(len(b))          print(len(b))
```

The reference fixes clone because that is how I authored them, not because
the defect class demands it. Auto-cloning would therefore insert copies where
a reordering was available and free. The pro-inversion case rests on a
measurement of my own authoring habits.

**2. Linearity catches real logic errors that value semantics would execute
as written.** `accumulate-without-reassign` is the one class where cloning is
silently wrong: the loop stops accumulating and the program prints the wrong
answer. Under value semantics that program compiles and faithfully does the
wrong thing. It is 1 of 20 here, but building a collection in a loop is among
the most common imperative idioms — its real-world frequency is far higher
than its corpus share. Trading a compile error for a silent wrong answer is
the wrong direction for this specific, common bug.

**3. The benefit being optimised for is modest and shrinking with
capability.** The measured advantage of implicit over explicit ownership,
isolated from ergonomics, is **≈ +10pp** — and it is **0.0pp at frontier**,
where both arms ceiling. Inverting the semantics of the language is a large,
hard-to-reverse change made to serve a capability band that is moving.

**4. The leverage is in ergonomics, not semantics.** The single largest
measured improvement in this project came from adding builtin method syntax:
oxide repair went 25.5% → 67.5% on one family, **+42pp**, four times the
entire ownership effect. It cost one parser function and changed no
semantics. If the goal is making Oxide writable by models, surface ergonomics
dominate ownership defaults by a wide margin, and they are cheap and
reversible.

**5. Silent deep copies conflict with the language's own pitch.** Oxide
transpiles to Rust and inherits its performance model. Implicit cloning
trades a compile-time error for an invisible runtime cost, in a language
whose reason for having linear types at all is to make that cost explicit.

## What is adopted instead

- **Linearity remains the default.** No `resource struct`, no value-semantics
  inversion, no opt-in linearity.
- **Ergonomics become the v0.3 track.** Method syntax (Part XI) is the
  template: measurable, semantics-preserving, cheap. The `OX0200` wall — 293
  name/type diagnostics against 163 linearity ones — is now the largest
  barrier by a factor of nearly two, and is where the next work belongs.
- **Diagnostics remain a first-class lever.** Fixing `OX0403` to name the
  later use flipped a frontier repair from fail to pass. `OX0304`'s
  suggestion is still wrong for the method-syntax case and should be fixed
  next.

## What would reopen this

A corpus of ownership defects authored so that cloning is the *only* correct
repair — independently, not by me — showing that linearity blocks programs
whose sole fix is a copy. That would move the argument from "the compiler
demands something the author would have wanted anyway" to "the compiler
demands friction with no alternative," which is the real case for inverting.

Until then the evidence says linearity is doing its job, and the friction
models experience is mostly elsewhere.
