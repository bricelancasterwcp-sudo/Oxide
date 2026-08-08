# `mut` acceptance (SPEC §54) — measured

**Change:** `let mut x = e` parses as `let x = e`. Contextual keyword, no
semantics.
**Design:** 20 classes × 3 arms × 10 seeds = 600 repairs per family, before
and after, same corpus and seeds. Two families: qwen (largest absolute
`OX0200` count) and granite (largest `OX0200` count overall).

## Results

| Family | arm | strict before | after | change |
|---|---|---|---|---|
| qwen | oxide | 67.5% | **73.0%** | **+5.5** |
| qwen | explicit | 14.5% | 14.0% | −0.5 |
| qwen | rust | 89.0% | 89.0% | 0.0 |
| granite | oxide | 22.0% | 20.5% | −1.5 |
| granite | explicit | 12.0% | 11.0% | −1.0 |
| granite | rust | 73.0% | 73.0% | 0.0 |

`OX0200` count on failing oxide submissions:

| Family | before | after | |
|---|---|---|---|
| qwen | 81 | **10** | **−71** |
| granite | 103 | 111 | +8 |

## The wall was two different things

qwen's `OX0200` was overwhelmingly the grammar artifact: accepting `mut`
removed 88% of it (81 → 10) and lifted repair 5.5pp. granite's `OX0200` did
not move at all (103 → 111, within noise) and its repair rate was flat.

So "the `OX0200` wall" was never one phenomenon. In qwen it was
`let mut acc` being glued into `let mutacc` by a decoder that cannot reject
tokens. In granite it is genuinely undeclared variables — models assigning to
a name never bound by `let`, Python-style implicit binding — which no
grammar or parser change addresses.

The pre-change mut-mangled share predicted this only loosely: qwen 42%,
codegemma 80%, granite 35% of `OX0200`-carrying submissions. granite had a
real share of mangling yet gained nothing, so the mangled submissions there
were failing for additional reasons that survived the fix.

## Effect on the primary comparison

| Family | before | after |
|---|---|---|
| qwen | +53.0pp `[+36.2, +69.8]` | **+59.0pp** `[+42.8, +75.2]` |
| granite | +10.0pp `[+3.4, +16.6]` | **+9.5pp** `[+3.8, +15.2]` |

Both still clear 2 SE. qwen's gap widened because the fix helped oxide and
not explicit — the same asymmetry method syntax showed, for the same reason:
these are ergonomic changes and the explicit dialect's binding constraint is
its annotation burden (`EX0002`), which they do not touch.

## Honest size

This is a **+5.5pp** change in one family and **nothing** in another,
against method syntax's +42pp. It was predicted to be smaller — it addresses
44% of one error class rather than eliminating a class outright — and it is.

Its real value is not the repair rate. It is that **the largest single cause
of the largest remaining error class was the measuring instrument deforming
model output**, and that is now fixed and documented (SPEC §54). Every error
count this project collected under grammar constraint carried that artifact.

Raw: `qwen2.5-coder-7b.jsonl`, `granite-code-8b.jsonl`.
