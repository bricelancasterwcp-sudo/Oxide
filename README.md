# Black Oxide

A Rust-like language with **implicit linear types**: ownership works like
Rust's, but there is no borrow syntax. The compiler infers moves, borrows, and
destruction points; types are fully inferred. Black Oxide transpiles to Rust, and
`rustc` is the oracle — any program Black Oxide accepts that `rustc` rejects is a
compiler bug.

```oxide
struct Reading { label: Str, values: Vec<Int> }

fn first_big(v: Vec<Int>) -> Int {
    let found = -1
    for x in v {
        if x > 10 {
            found = x
            break
        }
    }
    found
}

fn main() {
    let r = Reading { label: "lab", values: push(push(vec(), 3), 42) }
    print(first_big(r.values))        // field access copies; r stays usable
    let r2 = Reading { label: "lab2", ..r }
    print(str_len(r2.label))
}
```

No semicolons, no `&`, no lifetimes, no `mut`, no manual `drop`. The value in
`r.values` is passed, used, and destroyed without any of it being written down.

## Why it exists

The thesis is that **LLMs write Black Oxide more reliably than Rust**, because the
compiler does the ownership bookkeeping instead of the programmer.

That claim is deliberately falsifiable, and this repository contains the
apparatus built to falsify it — plus the results, including the ones that
didn't go the thesis's way.

## Where the thesis actually stands

**Partially supported, with a capability window.** Ordered by how well each
subject performs the task at all:

All at 10 seeds, 600 repairs per subject, matched code:

| Subject | Black Oxide | explicit Black Oxide | paired delta | 2-SE |
|---|---|---|---|---|
| Claude Opus 5 | 92% | 92% | **0.0pp** | ceiling, arms identical |
| qwen2.5-coder-7b | 73.0% | 14.0% | **+59.0pp** | `[+42.8, +75.2]` |
| codegemma-7b | 46.5% | 11.5% | **+35.0pp** | `[+19.7, +50.3]` |
| granite-code-8b | 20.5% | 11.0% | **+9.5pp** | `[+3.8, +15.2]` |

All three local families are post-`mut` (SPEC §54), on matched code.

**Every family clears 2 SE. Pooled: 41 of 43 non-tied comparisons,
p < 10⁻⁹. Combined over all 60 (family × class) pairs: +34.5pp,
2-SE `[+25.3, +43.7]`.** But see the decomposition below — most of that is
*ergonomic*, not about ownership reasoning.

The comparison that matters is **Black Oxide vs explicit Black Oxide** — a control dialect
with identical grammar, builtins, and diagnostics, where ownership is written
out by hand (`&` reads, declared parameter modes, mandatory `drop`). Both are
languages the model has never seen, taught only by a card of comparable
length. They differ in exactly one thing: whether ownership is implicit.

### The effect splits in two, and only one half is about ownership

Adding builtin method syntax (SPEC Part XI) moved these numbers enormously —
and the way it moved them is the finding:

| Model | `OX0304` before → after | oxide strict change |
|---|---|---|
| qwen | 94 → **0** | **+42.0** |
| codegemma | 72 → **0** | **+31.0** |
| granite | 9 → 12 | **−1.5** |

The change helped exactly the families that had the `.clone()` problem and did
nothing for the one that didn't. A dose-response across independent families
is strong causal evidence — and the opposite of the pattern that sank three
earlier headlines here.

But it means the large deltas are substantially **ergonomic**. Method syntax
composes with implicit ownership (`v.len()`) and awkwardly with explicit
(`(&v).len()`, since the receiver still needs its marker), so it lifts one arm
and not the other.

**granite is the clean estimate.** It never used method syntax, so its
**+10.0pp** isolates ownership from ergonomics — and it is unchanged from
before the change.

- **ownership effect alone: ≈ +10pp** — granite, which benefited from neither
  ergonomic fix, sits at +9.5pp
- **ergonomic effect: larger, family-dependent**, up to +42pp
- the combined **+34.5pp `[+25.3, +43.7]`** mixes the two and should not be
  quoted as an ownership result

### Two ergonomic fixes, and what each was worth

Both came from reading failing submissions rather than theorising, and both
show the same dose-response — they helped exactly the models that had the
problem:

| Fix | mechanism removed | effect |
|---|---|---|
| **Method syntax** (§53) | `.clone()` → `OX0304`, 94→0 and 72→0 | **+42pp** (qwen), **+31pp** (codegemma), **−1.5pp** (granite, which never used it) |
| **`mut` accepted** (§54) | `let mut acc` glued into `let mutacc`, `OX0200` 81→10 (qwen) and 42→**0** (codegemma) | **+5.5pp** (qwen), **+3.0pp** (codegemma), **0** (granite) |

The second is the more instructive. Chasing `OX0200` — the largest remaining
error class — found that its largest single cause was **the measuring
instrument deforming model output**. A grammar-constrained decoder cannot
reject a token; it steers to the nearest valid string, so `mut acc` became
the identifier `mutacc` and every later use of `acc` was an unknown name.
That accounted for 44% of `OX0200`-carrying submissions.

Every error count this project collected under grammar constraint carried
that artifact. SPEC §54 records the general hazard.

It also showed the "wall" was two different things: qwen's `OX0200` was 88%
grammar artifact and vanished; codegemma's was *entirely* artifact (42 → 0),
though clearing it bought only +3pp — gluing co-occurred with other defects;
granite's is genuinely undeclared variables —
Python-style implicit binding — and did not move at all. That residue is a
model-competence limit, not an ergonomics or instrument problem, and should
be expected to yield far less than the two fixes above.

### Why earlier estimates were lower, and three were withdrawn

Before method syntax, *both* primary arms were partly blocked by the same
`.clone()` barrier — incidental to the question. That floored oxide and
suppressed the measured difference: the pooled figure was 23 of 34, p = 0.058.
An ergonomic wart unrelated to ownership was making the eval underestimate its
own effect.

Three earlier headlines were withdrawn along the way, each a single-subject
result that dissolved under replication: a 3-seed "+18.3pp, resolved" that
became +10.0pp spanning zero at 10 seeds; the pooled significance that
followed it; and a "degenerate-fix rate" whose metric never required the
program to compile. The trajectory is recorded in
[`eval/results/ownership-probe-10seed/`](eval/results/ownership-probe-10seed/)
and [`eval/results/method-syntax/`](eval/results/method-syntax/).

**Supported:** implicit linearity is repaired more reliably than explicit
linearity in all three families, and the effect resolves. The ownership
component is around +10pp; the rest is ergonomic.

**Not supported:**

- *"Makes LLMs more reliable"* without qualification. At frontier the delta is
  exactly zero — a model that already reasons about ownership correctly gains
  nothing.
- A single magnitude. The per-family deltas span +10 to +53 and track how
  much the ergonomic change helped each model, not how well it reasons about
  ownership.
- Anything about **writing** Black Oxide. These models cannot. A 7B model scores
  **2/20** first-compile writing Black Oxide from the card, against 20/20 for Rust.
  That gap is pretraining exposure, not language design.

## How it was measured

Whole-program generation could not test the thesis at all. Across ~530
attempts in five configurations, **not one linearity diagnostic ever fired** —
semantic analysis is strictly staged, so ownership sits behind lexing,
parsing, name resolution, and type checking. Small models never cleared the
first two. Frontier models cleared all four and scored 20/20. The feature the
language exists for was reached by nobody.

So the instrument changed. The **ownership probe** hands the model a program
that is complete and correct except for **one ownership defect**, together
with the compiler's diagnostic, and scores the repair. Syntax, names, and
types are all supplied; the only thing wrong is the thing under test.

- 20 defect classes × 3 arms, every record mechanically verified: the broken
  form must fail with its intended ownership code **and nothing else**, and
  the reference fix must compile and reproduce the expected output.
- Scored **strict** (compiles *and* output matches) and **lenient** (parses and
  the ownership diagnostic is gone). The gap between them is the
  gap between them is *not* a degenerate-fix rate, though it was reported as
  one here until it was checked directly — see the correction below.
  On lenient alone the three arms are indistinguishable; strict separates them
  completely. Reporting lenient without strict inverts the conclusion.

## A correction

This README previously reported a "degenerate-fix rate" — repairs that
compile, silence the ownership error, and silently do the wrong thing —
at **38–68% in the Black Oxide arms against 3–7% for Rust**, and called it the most
solid result the instrument had produced. **That was wrong.**

It was computed as `lenient AND NOT strict`. But `lenient` requires only that
a submission *parses* and carries no ownership code — it does not require the
program to **compile**. Most of those repairs traded an ownership error for a
*type* error and never ran at all. Checking directly:

| Model | oxide | explicit | rust |
|---|---|---|---|
| qwen2.5-coder-7b | **0%** | **0%** | 3% |
| codegemma-7b | 2.5% | 2.5% | 5% |
| granite-code-8b | **34%** | 25.5% | 2% |

Two families put Rust *highest*; one puts Black Oxide highest. There is no
consistent direction and no order-of-magnitude gap. The claim is withdrawn.

What the mislabelled repairs actually were is more interesting: in the qwen
Black Oxide arms the dominant failures are `OX0304` (94) and `OX0200` (86) — method
syntax like `v.clone()`, which Black Oxide does not have, and undefined names. Even
in a *repair* task, with syntax, names, and types all supplied and only the
ownership decision missing, these models reach for Rust idioms the language
does not provide. That matches the whole-program result and is the more
durable observation.

`score()` now emits an explicit `degenerate` field requiring compilation, with
tests pinning that a type error can never be counted as one.

## It found a real bug

`OX0403` (loop-carried move) named only the move site. In a loop the move and
the conflicting use are the same syntax, so the diagnostic pointed at one
location twice and never mentioned the use *after* the loop — the thing that
makes the move fatal. `rustc` names both ends.

A frontier model, given the old diagnostic, cloned inside the loop — which
`OX0403`'s own suggestion recommended — producing a program that compiles,
silences the error, and never accumulates. It repaired the Rust version
correctly. Fixing the diagnostic flipped that probe from fail to pass in both
Black Oxide dialects.

At 7B the same fix changes nothing — oxide strict is 25.5% before and after,
across 600 repairs each way. Causal at frontier, inert at 7B; both are
measured, and the fix stands on its own merits either way, since the old
diagnostic named one location twice and omitted the use that makes the move
fatal.

## Quick start

Requires Python 3.14 and a Rust toolchain. No third-party Python dependencies.

```bash
python3 -m venv .venv && .venv/bin/pip install pytest pytest-cov
.venv/bin/pytest tests/ -q                 # 1408 tests
```

```bash
python3 main.py program.ox                 # emit Rust to stdout
python3 main.py --check program.ox         # type/ownership check only
python3 main.py --json program.ox          # machine-readable diagnostics
python3 main.py --dialect=explicit p.ox    # the explicit-ownership control
```

Evaluation harness:

```bash
python3 -m eval.harness prompt --arm oxide --task t01
python3 -m eval.harness run --arm oxide --file solution.ox --task t01
python3 -m eval.probe --help               # the ownership probe

# the §56 deformation signature over a run root's oxide arm, per family
python3 -m eval.deformation eval/results/g0-generation-baseline/constrained
```

`eval/deformation.py` carries the pinned counting definition for that
signature; its numbers are a pre-registered endpoint and are reproduced
over the committed corpus by `tests/test_g0_report.py`. The count is a
lower bound on deformation, for the reason the module's docstring and
SPEC §56 both give.

Live-model tests are marked and deselected by default; run them with
`-m live`.

## Layout

| Path | |
|---|---|
| `SPEC.md` | the binding contract — every phase is a numbered normative Part |
| `src/` | lexer, parser, semantic analysis, Rust codegen, CLI |
| `src/explicit/` | the explicit-ownership control dialect |
| `eval/` | harness, task corpus, ownership probe, grammars, model clients |
| `eval/results/` | every run, with raw model outputs verbatim |
| `LANGUAGE_CARD.md` | what a model is given — compiler-validated |
| `docs/superpowers/specs/` | design documents |

## Status and honest limits

Black Oxide is a research vehicle, not a usable language. It has no generics,
traits, closures, modules, tuples, indexing syntax, or sized integer types.
It is a strict subset of what it compiles to.

Every result here is one shot condition on a 20-class corpus, with all three
local subjects at 10 seeds. The frontier subject is at one seed — it ceilinged,
so more would not discriminate, but the 0.0pp figure rests on 12 classes. The reports in `eval/results/` state their own limits,
including which conclusions the data does *not* support and which numbers
sit at a floor or ceiling where the design has no resolution.

`eval/results/*/REPORT.md` is the place to start if you want the full picture
rather than the summary.

## License

MIT — see [LICENSE](LICENSE).
