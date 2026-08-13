# Constrained decoding deforms rather than rejects

*Black Oxide findings series — 2026-08-12. All numbers are reproducible
from this repository; sources are linked inline.*

## The claim

A grammar-constrained decoder cannot reject a token the model wants to
emit. It can only steer generation to the nearest string the grammar
admits. The result is not an error — it is a program that **parses and
means something the model did not write**. Any error count, pass rate,
or failure taxonomy collected under grammar constraint therefore
contains artifacts manufactured by the grammar's own gaps, and cannot be
read as a measurement of the model alone.

This is easy to state and easy to nod along to. What this project adds
is magnitude and a clean control: measured across three model families,
a single deformation artifact accounted for **44% of the largest
remaining error class**, and a second artifact appeared **18 times in
600 constrained programs and exactly zero times in 600 unconstrained
ones**.

## Instance 1: `let mut acc` → `let mutacc` (SPEC §54)

Models write `let mut x` reflexively — Rust's most common binding form.
Black Oxide (pre-§54) had no `mut`, so the GBNF grammar did not admit it.
The decoder could not reject the token; it glued the fragment into the
nearest admissible string: `let mut acc` became **`let mutacc`**, a
legal binding of a variable named `mutacc` — and every later use of
`acc` became an undefined-name error (`OX0200`).

Measured across qwen2.5-coder-7b, codegemma-7b, and granite-code-8b:
that one artifact accounted for **44% of OX0200-carrying submissions**
— the largest cause of the largest remaining error class. The fix was
to accept and discard `mut` at the parser ([SPEC §54](../../SPEC.md)),
which removed the mechanism outright: `OX0200` counts fell 81→10 (qwen)
and 42→0 (codegemma), worth +5.5pp and +3.0pp strict repair — and 0 on
granite, which never wrote `mut` in the first place. The fix helped
exactly the families that had the problem: the dose-response form this
project uses as its causal evidence standard.

## Instance 2: `p.x = 5` → `p.x == 5`, with a clean control (SPEC §56)

Pre-§56, Black Oxide had no field assignment; `=` was inadmissible after a
field path. Models want to assign fields in place, and under constraint
that want does not fail loudly: the decoder settles on the nearest legal
token, `==`, and emits a **discarded comparison** — a well-formed
program that silently does nothing where the model intended a write.

The control makes this instance decisive. Counted over the committed G0
first attempts by [`eval/deformation.py`](../../eval/deformation.py):
the signature appears **18 times in 9 of 600 constrained programs, and
exactly 0 times in 600 unconstrained programs** from the same families
on the same tasks. The artifact is manufactured entirely by the
constraint. (18 is a lower bound by the tool's pinned definition —
statement position only; tail position is ambiguous in both directions
and deliberately not pooled. See SPEC §56.) After the fix, the
statement and tail signatures (18 and 17 occurrences) both went to 0.

SPEC §56 calls this "the §54 lesson in its third demonstrated instance"
— by the time the project closed v0.3, the same shape had recurred
often enough to be treated as a standing hazard class rather than a
curiosity.

## Why this matters beyond one research language

1. **Every constrained-decoding benchmark number carries this
   artifact.** If you evaluate a model through GBNF, JSON-schema mode,
   or any token-masking constraint, some fraction of its "errors" (and
   some of its silently-wrong successes) are the constraint's, not the
   model's. This project's headline repair results are reported with
   that caveat attached (SPEC §54), and its whole-program generation
   arms were measured constrained and unconstrained precisely so the
   deformation could be isolated.
2. **JSON mode fails silently in the worst way.** The deformation class
   predicts confidently-wrong structured output rather than loud
   failure: the payload is valid JSON whose *content* was steered. Two
   sibling projects arrived at the same law independently and encoded
   it as design rules: [robigo](https://github.com/bricelancasterwcp-sudo/robigo)
   (a VRAM-budgeted repair agent) constrains the **envelope, never the
   payload** — a grammar-forced code payload corrupts silently, so its
   levels are "no grammar", "two-step: header constrained, payload
   free", or "refuse";
   [assay](https://github.com/bricelancasterwcp-sudo/assay) (a
   capability prober) measures JSON emission **unforced**, because a
   forced probe measures the constraint, not the model.
3. **The mitigation is language/schema design, not decoder repair.**
   Both instances above were fixed by admitting the token the models
   insist on writing (accept-and-discard `mut`; add field assignment)
   — meeting the pretraining prior instead of fighting it. Where you
   cannot change the format, the mitigation is to leave the payload
   unconstrained and validate after, so failure is loud.

## Honest limits

- The 44% figure is a share of failing submissions carrying the
  artifact, measured on this project's eval corpus and grammar; the
  transferable claim is the mechanism and its scale, not the exact
  percentage.
- Removing deformation is not the same as improving pass rates: §56
  eliminated its artifact outright while moving aggregate rates within
  noise (the family with the most deformation to remove moved least —
  SPEC §33's v0.3 close). Deformation corrupts *measurement* more
  reliably than it suppresses *scores*.
- This repository records three withdrawn headline claims (README,
  "Why earlier estimates were lower, and three were withdrawn"); none
  of the numbers in this document are among them, and all are
  recomputable from committed artifacts.
