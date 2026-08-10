# g3 — the `to_str` alias, and the v0.3 closing baseline

**Date:** 2026-08-10
**Track:** v0.3 generation ergonomics, taxonomy dossier 3 — **narrowed on
measurement**, see below.
**Class:** builtin alias. No new semantics, no new codegen machinery.
**Status:** design approved in session; SPEC amendment and implementation
plan follow.

## Dossier 3 did not survive contact with its own evidence

The taxonomy ranked dossier 3 as "conversion builtins (`to_int`, `to_str`)
and their receiver forms", with the fix: *add `to_str(Int|Float) -> Str`
and `to_int(Str) -> Option<Int>`*.

Two of those three claims are wrong, and the third is right for a different
reason than stated. Measured over the 600 constrained oxide first attempts
in `eval/results/g0-generation-baseline/`:

### The conversions are not missing

`int_to_str(Int) -> Str` and `parse_int(Str) -> Option<Int>` **already
exist** in `src.sema.types.BUILTINS`, and both are **already taught on the
language card** (`LANGUAGE_CARD.md`, the builtin table). The proposed
`to_int(Str) -> Option<Int>` duplicates `parse_int` exactly. So the friction
was never absent functionality; at most it is absent *spelling*.

### What models actually reach for

Classifying every call site by what it is called **on**:

| name | sites | dominant form | genuine plain calls |
|---|---|---|---|
| `to_string` | 72 | **63.9% string-literal receiver** — `"lit".to_string()` | **4** |
| `to_int` | 51 | **70.6% int-literal receiver** — `2.to_int()` | **2** |
| `to_str` | 42 | **85.7% plain call** — `to_str(x)` | **21** (+15 definitions) |

- **`to_string` is a Rust idiom with no Black Oxide meaning.** `&str` →
  `String` is the overwhelming use, and `Str` here is already owned, so the
  operation is an identity function. Adding it would mean shipping a no-op
  builtin purely to absorb a habit.
- **`to_int` is not a conversion at all.** Seven in ten uses are on an
  *integer literal*, inside malformed `for` headers — `for i in
  2.to_int().range(x)`, `for x in 0.to_int().until(...)`. That is the
  **`2.to(n)` range demand** already recorded in the deferred ledger for
  v0.4, wearing a conversion's name. `to_int(Str) -> Option<Int>` would
  address at most the 23.5% variable-receiver slice, and `parse_int` already
  covers that slice today.
- **`to_str` is the real finding**, and the strongest signal in the scan is
  not the calls but the **definitions**: `fn to_str(...)` is written by the
  model itself **15 times across 6 programs**. A model defining a function
  is a language telling you it lacks a name its users want.

### A measurement caution this scan produced

Raw occurrence counts are unusable here. Counting occurrences gives qwen
`to_int` = 291 and granite `to_vec` = 337; counting **distinct programs**
collapses each to **1** — both are the degenerate whole-program repetition
the taxonomy discounts elsewhere, and neither is evidence of anything. Every
number above is a program-level or classified-site count for this reason.

Program counts, out of 200 per family: `to_string` 23 (cg 16 / gr 5 / qw 2),
`to_int` 14 (cg 11 / gr 2 / qw 1), `to_str` 9 (cg 4 / gr 4 / qw 1).

## The change

Add `to_str` as a **second name for the existing `int_to_str`**.

```python
# src/sema/types.py — signature identical to int_to_str
"to_str": BuiltinSig(params=(INT,), ret=STR, modes=("read",), generics=()),

# src/codegen/support.py — BUILTIN_REF, and one more prelude function
"to_str": (False,),
fn to_str(x: i64) -> String { x.to_string() }
```

`int_to_str` **stays**. Renaming it would break the card and every committed
reference solution in `eval/solutions/`.

**`Int -> Str` only, not `Int|Float -> Str`.** The taxonomy's signature is
not expressible: Black Oxide has no type-based overloading — the taxonomy
itself files that as a deferred non-candidate — so a single name cannot take
both. `Float -> Str` remains reachable as `to_str(trunc(x))`, and no
observed call site asked for it.

The §53 receiver form `n.to_str()` comes free: the parser's builtin-method
name set mirrors `BUILTINS` mechanically.

## Cards unchanged

Both cards stay exactly as they are, matching g1 and g2 — and here it is the
*correct* test rather than merely the cheap one. Models reach for `to_str`
**without being taught it**, to the point of defining it themselves. Adding
a card row would measure teaching, not acceptance, and acceptance is the
question.

This is a choice rather than a constraint: the builtin table is fenced
` ```text ` and is therefore exempt from the 900-word cap (`tests/
test_cards.py`). For the record, the current state is core **895/900**,
explicit 980, the two **8.7%** apart against a 10% band — so a row would
have fit.

## The campaign, and what it is for

**Primary purpose: the v0.3 closing baseline.** The direction memo's pivot
is a token-matched LoRA compared against the shipped language (SPEC §32.4).
That comparison needs a measured starting point at final v0.3 HEAD, and this
run produces it. The run happens for the pivot whether or not g2 and g3 move
anything.

Design: the constrained G0 grid re-run at v0.3 HEAD — 3 families × 3 arms ×
20 tasks × 10 seeds, same seeds, same windows (granite 4096), GBNF
constrained. g2 and g3 ride along as embedded code-level checks.

### Pre-registered endpoints, written before the run

| endpoint | prediction |
|---|---|
| **v0.3 baseline table** (all arms, all families) | descriptive — this is the deliverable, not a hypothesis |
| g2: ExprStmt-position `f.x == e`, constrained oxide | **18 → 0**, cited as a **lower bound** (a deformation falling last in a function tail-converts out of the count) |
| g2: the same signature, unconstrained | 0 → 0 |
| g3: `to_str`-shaped `OX0306` | **→ 0**, mechanically — the name now resolves |
| g3: `fn to_str` self-definitions | should fall from 15 across 6 programs |
| aggregate first-attempt pass rates | **no detectable change** — both changes sit at ~1.5–2% prevalence and cannot move them; apparent movement is noise |
| rust arm | flat at the first-attempt **rate** level, never the byte level |

### A prediction deliberately NOT made

The taxonomy predicted `OX0306` would fall "codegemma ≫ qwen > granite — a
family-ordered dose-response matching the demand counts". **That is not
testable at this scale.** `to_str` appears in 4 / 4 / 1 programs across the
three families; there is no ordering resolvable in n = 9, and asserting one
would repeat the 3-seed error that produced two withdrawn headlines. The
dose-response language belongs to g1's `vec(...)`, where the counts were
91 / 69 / 27 and all three families moved in the predicted order.

## What this run does not show

- **It does not test whether teaching `to_str` helps.** Cards are unchanged
  by design; this measures acceptance only.
- **It does not address the `to_int` demand**, which is range sugar and
  belongs to the v0.4 ledger's `2.to(n)` entry.
- **It does not address `to_string`**, which is a no-op in this language.
- **A null on g2/g3 is not a failed run.** The campaign's primary output is
  the v0.3 baseline. This is stated before the data exists precisely so it
  cannot be claimed afterwards.

## Follow-up this design creates

The taxonomy (`docs/superpowers/specs/2026-08-09-v03-taxonomy.md`, dossier
3) records a fix that the evidence does not support. It needs a correction
noting that `to_int`/`to_string` were dropped on measurement and why —
recorded here so the taxonomy is not left asserting a superseded plan, in
the same idiom as the withdrawn-claims log.
