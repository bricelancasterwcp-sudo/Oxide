# Ownership Probe — design

**Date:** 2026-08-07
**Status:** design. Supersedes whole-program generation as the instrument for
the v0.3 gate.

## 1. Why the existing instrument cannot answer the question

Phase 6a measured whole-program generation across three arms. Across ~530
Oxide-arm attempts in five configurations — 0.5B and 7B, 0-shot and 3-shot,
unconstrained and grammar-constrained — **not one `OX04xx` linearity
diagnostic has ever been observed** (`eval/results/6a-pilot/REPORT.md`).

`src/sema/analyze.py` is strictly staged. Generation must clear lexing,
parsing, name resolution, and type checking before linearity is even
evaluated. Small models never clear the first two; grammar constraint removed
that barrier and the population simply moved to the third. Frontier models
clear all four effortlessly and score 20/20, so linearity never fires there
either.

The feature the language exists for is therefore reached by nobody: it sits
behind three competencies at the bottom and below the noise floor at the top.

## 2. The instrument

Give the model a program that is **complete and correct except for one
ownership defect**, together with the compiler's diagnostic, and ask it to fix
the program. Score the repair.

This removes every barrier that is not ownership: syntax, names, and types are
all supplied and correct. The only thing wrong is the thing under test.

### Matched triples

Each probe is one ownership mistake expressed in all three arms — the same
underlying defect, the same program behaviour, the same expected output. A
verified example (mechanically checked, all three arms):

```
# oxide — broken (OX0400)          # oxide — fix
let a = push(push(vec(), 1), 2)    let a = push(push(vec(), 1), 2)
let b = a                          let b = clone(a)
print(len(a))                      print(len(a))
print(len(b))                      print(len(b))

# rust — broken (E0382)            # rust — fix
let a = vec![1, 2];                let a = vec![1, 2];
let b = a;                         let b = a.clone();
println!("{}", a.len());           println!("{}", a.len());
println!("{}", b.len());           println!("{}", b.len());
```

explicit-Oxide carries the same defect plus its own annotation burden: `&`
reads and `drop` statements placed at exactly the checker's computed points
(`drop a` immediately after `a`'s last use, not at scope end — misplacement is
`EX0004`, omission is `EX0003`).

Expected stdout for all three: `2\n2\n`.

## 3. Corpus contract — `eval/probes.jsonl`

One record per (defect, arm):

```json
{"id": "p01", "arm": "oxide", "defect": "use-after-move",
 "expected_code": "OX0400", "expected_stdout": "2\n2\n",
 "broken": "...", "fix": "..."}
```

**Both halves are mechanically verified, and this is the corpus's whole
claim to validity:**

1. `broken` must FAIL to compile, and its diagnostics must contain
   `expected_code`. A probe whose broken form fails for an unrelated reason
   is measuring the wrong thing.
2. `broken` must fail for the ownership reason ONLY — no `OX0001`, no
   `OX01xx`, no `OX02xx`, no `OX03xx`. A probe carrying an incidental syntax
   or type error reintroduces the confound this instrument exists to remove.
3. `fix` must compile AND produce `expected_stdout` exactly.
4. Across arms, a given defect id must share `expected_stdout` — the three
   programs must do the same observable thing.

These are enforced by tests, in the same spirit as the existing 60 reference
solutions.

### Defect classes to cover

| Class | Oxide | Rust |
|---|---|---|
| use-after-move | `OX0400` | `E0382` |
| double consume | `OX0401` | `E0382` |
| loop-carried move | `OX0403` | `E0382` |
| assign to iterated vec | `OX0406` | `E0502` |
| move then read field | `OX0400` | `E0382` |
| move inside branch | `OX0400` | `E0382` |

At least six defects, one per class where the class exists in both languages.
Where a class has no faithful Rust equivalent, say so in the record rather
than forcing an approximation.

## 4. Prompt

The arm's full language card, then the broken program, then its diagnostic
rendered exactly as `eval/repair.py` renders it, then the fix instruction.
The card is included deliberately: this probe isolates ownership, so card
recall must not be a second variable.

`expected_stdout` is never disclosed — the same integrity requirement as the
repair prompt. A model told the expected output could pass by printing it.

## 5. Scoring

Two scores, both reported:

- **Strict** — the repaired program compiles and its stdout matches
  `expected_stdout`. This is the headline. Requiring the output prevents the
  degenerate fix: deleting the offending use compiles cleanly and would score
  as a repair while silently changing what the program does.
- **Lenient** — the ownership diagnostic class is gone, regardless of any
  other error introduced. This separates "understood the ownership fix" from
  "could also still write valid code", which matters at small scale.

Report both per arm, plus the diagnostic distribution of the repaired
programs.

## 6. What this can and cannot show

**Can:** whether a model, handed a correct program with one ownership defect
and the compiler's explanation, repairs it — and whether implicit linearity
(Oxide) is repaired more reliably than explicit linearity (explicit-Oxide) at
matched novelty. That is the thesis, tested directly.

**Cannot:** whether a model can write Oxide from scratch. Phase 6a already
answered that — at ≤7B, no. This instrument deliberately supplies everything
except the ownership decision, so it measures comprehension and repair of
ownership, not production of programs.

Report both results together. Neither replaces the other.

## 7. Non-goals

No new language features, no changes to `src/`, no changes to
`eval/harness.py`. The probe corpus and its runner are additive.
