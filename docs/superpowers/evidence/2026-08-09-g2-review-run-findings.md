# What the g2 review run found about its own tests

**Date:** 2026-08-09
**Scope:** the review record for SPEC §56 (field assignment), executed as
seven tasks with a per-task review gate and a final whole-branch review.
**Why this file exists:** the same reason the withdrawn-claims log exists.
The failures below are not embarrassing footnotes — they are the most
transferable result of the run, and they were all invisible to a green
test suite.

## The headline: eight tests that pinned nothing

Eight times, a test that looked like it pinned a behaviour **passed against
a deliberately broken implementation**. In every single case the
implementation was correct and the test was not. Every one was caught only
by mutating the source and re-running — never by reading the test.

| # | The test | The mutation it survived |
|---|---|---|
| 1 | nested-path walk, `o.i.nope` | check every segment against the BASE type instead of threading it |
| 2 | no-re-own contrast | append a `ReInit` to the `FieldAssign` CFG case |
| 3 | RHS-is-a-move | flip the RHS lowering context `_MOVE` → `_READ` |
| 4 | dialect parity | delete `strip.py`'s `ast.FieldAssign` case |
| 5 | place-write emits no clone | append `.clone()` to the assignment target |
| 6 | keyword-field escaping | drop `escape()` from the emitted path |
| 7 | walker sees the RHS | delete `support.py`'s `ast.FieldAssign` case |
| 8 | place write, *nested* path | clone the intermediate segment (see below — rustc **accepts** this one) |

Findings 5–7 all emit Rust that `rustc` **rejects** (E0070, a keyword
error, E0369) — i.e. the project's central accepted-implies-compiles
invariant had three unguarded holes that a full green suite could not see.

## The recurring cause: a fixture too simple to reach the branch it names

Every instance has the same shape. The test's *assertion* was fine; its
*fixture* could not reach the code path under test.

- **`o.i.nope`** — `nope` is absent from *both* the outer and inner struct,
  so a base-only walk still emits `OX0304`: the right code for the wrong
  reason. Fixed with `o.i.i`, where `i` exists on the outer struct but not
  the inner one, so a correct walk gives `OX0304` and a broken one `OX0300`.
- **`p.x = 5` with `x: Int`** — §36's implicit `.clone()` is
  **type-conditional**, so on a Copy field the correct emitter and the
  clone-appending one produce byte-identical output. The test asserted
  `"p.x.clone() = " not in rust`, a string the real defect never emits.
  Fixed with a non-copy (`Str`) field.
- **A strip fixture whose RHS is a bare literal** — stripping a literal is
  a no-op, so the correct path and the missing-case fall-through returned
  structurally identical nodes. Fixed with an RHS carrying a `drop` inside
  a nested block.
- **Poisoning masking state corruption** — the no-re-own rule could not be
  observed *at all* through diagnostics: the base's `Use(_READ)` fires
  `OX0400` and poisons the variable before the spurious `ReInit` runs, and
  `check_linear` discards every `DropPoint` for a function with
  diagnostics. Exact-list equality would not have helped. Fixed with a
  white-box test asserting on the lowered CFG, which is the level the SPEC
  states the rule at.

## The operational lesson

**"The tests pass" is not evidence that the tests work.** For any task
whose deliverable is *pinning* existing behaviour, mutation verification
is the acceptance criterion, not a nicety: break the thing on purpose,
confirm the test fails, restore. Three of these were caught by reviewers
who mutated rather than read; the other four were caught only because
mutation was subsequently made standard procedure for the rest of the run.

A corollary for fixture design: when a rule is conditional (on a type, on
a node shape, on a position), the fixture must exercise the condition. A
Copy field cannot test a rule about non-copy values.

## Plan-authoring lessons

Three fixtures in the plan itself were wrong, and each was caught only
because a reviewer or implementer refused to "fix" the code to match:

1. **`take(p)` does not move `p`.** A parameter that is never assigned and
   never move-used infers mode `read` (§15/§28) and is emitted as `&p`.
   The genuine move is `let q = p`.
2. **Bare core source is correctly *rejected* by the explicit dialect**
   (`EX0003` + `EX0002`). The parity fixture needs the annotations written
   in — that rejection is the dialect working, not a bug.
3. **A lone comparison in a block tail-converts**, so it is never an
   `ExprStmt`. Tail conversion here is syntactic and unconditional.

Verify fixtures against the real pipeline before writing them into a plan.

## The eighth instance — CLOSED (`2c22c02`)

**The place-write guard was pinned only for single-segment paths.** Mutating
the emitter to clone an *intermediate* segment yields `o.i.clone().n = 5;`,
which passed the entire suite **and `rustc` accepts it**.

This one deserves its own entry, because it is not the same failure as the
other seven. The two clone failures are *not* equivalent:

| target | emitted | rustc |
|---|---|---|
| single segment | `p.name.clone() = s;` | **REJECTS** — E0070, not a place |
| intermediate | `o.i.clone().n = 5;` | **ACCEPTS** — it *is* a place |

A cloned intermediate compiles with only an `unused_mut` warning and the
write vanishes into the temporary. So this is the one §56 defect class that
**escapes the `rustc` oracle entirely** — accepted-implies-compiles is no
defence, because the emitted Rust is valid and merely wrong. Every prior
codegen guard here could lean on rustc rejecting the mutant; this one
cannot.

**Closed** by `tests/test_codegen_field_assign.py` section 4: a nested
fixture with a non-copy intermediate (every user struct is non-copy, so
`o.i` is exactly the shape §36 clones on a read) plus a **runtime stdout
assertion**. Mutation-verified: under the clone-the-intermediate mutant,
`rustc` exits 0, the program prints `1` instead of `5`, and **the other
1399 tests all pass** — these two are the sole killers, which is the
measurement of how much coverage there was before.

The transferable rule: **when the oracle cannot see the defect, the test
must assert on behaviour, not on acceptance.** "It compiles" was
insufficient here by construction.

## Also recorded

- `eval/grammar/build.py` was already 846 lines before this work (now 857),
  over the project's 800-line convention. Ruled out of scope for g2: it is
  a generated-grammar table whose output is byte-pinned by
  `tests/test_6a.py`, so splitting it churns a frozen artifact for no gain.
  Either scope the convention to exempt it or open a dedicated refactor —
  what must not happen is the next plan restating the constraint while
  editing this file, which is how a constraint stops meaning anything.
