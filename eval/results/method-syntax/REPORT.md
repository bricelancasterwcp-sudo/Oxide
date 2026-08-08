# Builtin method syntax — three-family effect

**Date:** 2026-08-07
**Change under test:** SPEC Part XI §53. `recv.name(args)` parses as
`name(recv, args)` for builtins. Sugar only — the parser emits an ordinary
`Call`, so resolution, use-context classification, linearity and codegen are
untouched.
**Design:** 20 classes × 3 arms × 10 seeds = 600 repairs per family, 1800
total. Same corpus, same grammars, same seeds before and after.

## Results

| Model | oxide before→after | explicit before→after | rust | delta | 2-SE | clears 2 SE |
|---|---|---|---|---|---|---|
| qwen2.5-coder-7b | 25.5 → **67.5** | 15.5 → 14.5 | 89.0 → 89.0 | **+53.0** | `[+36.2, +69.8]` | yes |
| codegemma-7b | 12.5 → **43.5** | 8.5 → 11.0 | 84.5 → 84.5 | **+32.5** | `[+16.5, +48.5]` | yes |
| granite-code-8b | 23.5 → **22.0** | 13.5 → 12.0 | 73.0 → 73.0 | **+10.0** | `[+3.4, +16.6]` | yes |

**Pooled: 41 of 46 non-tied comparisons favour implicit linearity,
two-sided exact p < 0.000001. Combined over all 60 (family × class) pairs:
+31.8pp, 2-SE `[+22.7, +41.0]`.**

Every family clears 2 SE individually. That had not happened before.

## The causal evidence is a dose-response

| Model | `OX0304` before → after | oxide strict change |
|---|---|---|
| qwen | 94 → **0** | **+42.0** |
| codegemma | 72 → **0** | **+31.0** |
| granite | 9 → 12 | **−1.5** |

The change helped exactly the families that had the problem and did nothing
for the one that did not. granite barely used `.clone()`; its wall is
`OX0200` (103 undefined names), a different barrier. A dose-response across
independent families is much stronger causal evidence than any single-family
before/after, and it is the opposite of the pattern that sank three earlier
headlines in this project.

## What the number is, and is not

The magnitude tracks how much method syntax helped, so the large deltas are
substantially **ergonomic** rather than about ownership reasoning. Method
syntax composes cleanly with implicit ownership and awkwardly with explicit:
a read is `v.len()` in Oxide but `(&v).len()` in explicit-Oxide, because the
receiver still needs its marker. The sugar therefore lifts one arm and not
the other.

That is a real language-design finding — *implicit ownership composes with
receiver-first syntax; explicit ownership does not* — but it is a different
claim from "models reason better about implicit ownership."

**granite is the clean estimate.** It never used method syntax, so its
**+10.0pp** is uncontaminated by the ergonomic change, and it is unchanged
from the pre-change measurement (+10.0 then as well, with a wider interval).

So the honest decomposition:

- **ownership effect alone: ≈ +10pp**, now resolving in the family that
  isolates it
- **ergonomic effect: much larger and family-dependent**, up to +42pp
- the headline **+31.8pp combines the two** and should not be quoted as an
  ownership result

## Why the earlier estimates were low

Before this change, both primary arms were partly blocked by the same
`.clone()` barrier — incidental to the question being asked. That floored
oxide and suppressed the measured difference. The pre-change pooled figure
was 23 of 34, p = 0.058.

An ergonomic wart unrelated to ownership was causing the eval to
underestimate its own effect. Fixing the language changed the measured
science.

## Scope

One shot condition, 10 seeds, 20 classes, three ~7B local models. Repair,
not authorship. The Oxide arms are grammar-constrained; Rust is not,
justified by Rust's unaided 100% parse rate. The frontier subject ceilings
at 0.0pp and is not included here.

Raw: `qwen2.5-coder-7b.jsonl`, `codegemma-7b.jsonl`, `granite-code-8b.jsonl`.
