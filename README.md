# Black Oxide

**A language built for verifier-in-the-loop repair.**

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

Models **repair** implicit linearity far better than they repair explicit
linearity. Hand one a correct program carrying a single ownership defect
plus the compiler's diagnostic, and ask for a fix: **+59.0pp** on
qwen2.5-coder-7b, **+35.0pp** on codegemma-7b, **+9.5pp** on
granite-code-8b — three families, 600 repairs each, pooled p < 10⁻⁹. Both
arms are languages the model has never seen, taught by cards of matched
length, differing in exactly one thing: whether ownership is implicit.

That advantage has a ceiling, and the ceiling is part of the claim. Claude
Opus 5 repairs both arms identically (11/12 each, **0.0pp**) — a model that
already reasons about ownership correctly gains nothing. This is a
small-model result, and the decomposition below shows only about **+10pp**
of it is ownership rather than surface ergonomics.

Models do **not** write Black Oxide better than they write Rust. Asked to
generate whole programs from a card under grammar-constrained decoding,
they pass on the first attempt 57/45/42% of the time in Rust against
26/14.5/9% in Black Oxide. That gap is pretraining exposure, not language
design, and the v0.3 ergonomics work barely moved it.

So the claim this repository is built to test is narrower, and more useful,
than "models write it better":

> **Implicit linearity pays in the repair loop** — a model proposes, a
> fail-closed compiler objects with a specific diagnostic, the model fixes.
> That is the loop real tooling runs, and it is where the evidence is.

Whether the advantage survives once pretraining exposure is equalised is
the next experiment: a token-matched fine-tune of Black Oxide against Rust
(SPEC §32.4). Everything needed to falsify any of this is in the
repository — including the results that went against the thesis, and a log
of three headline claims withdrawn when they failed to replicate.

## Standalone write-ups

Two findings from this project are written up as self-contained
documents, each with its numbers, method, controls, and limits:

- **[Constrained decoding deforms rather than
  rejects](docs/findings/2026-08-12-constrained-decoding-deforms.md)** —
  a grammar can only steer generation to the nearest legal string, so it
  manufactures programs the model did not write: one artifact accounted
  for 44% of the largest error class, another appeared 18 times in 600
  constrained programs and zero in 600 unconstrained. Independently
  corroborated by two sibling projects
  ([robigo](https://github.com/bricelancasterwcp-sudo/robigo),
  [assay](https://github.com/bricelancasterwcp-sudo/assay)).
- **[Most of the win was ergonomics, not
  ownership](docs/findings/2026-08-12-ergonomics-beat-ownership.md)** —
  the +59pp headline decomposes to ≈+10pp ownership and up-to-+42pp
  surface ergonomics under a matched-novelty control, vanishing at
  frontier capability; with the design guidance that follows.

## Where the evidence stands

**Partially supported, with a capability window.** Ordered by how well each
subject performs the task at all:

The three local families are at 10 seeds, 600 repairs each, on matched code.
The frontier row is a **different instrument** — see the note under the table.

| Subject | Black Oxide | explicit Black Oxide | paired delta | 2-SE |
|---|---|---|---|---|
| Claude Opus 5 † | 92% | 92% | **0.0pp** | ceiling, arms identical |
| qwen2.5-coder-7b | 73.0% | 14.0% | **+59.0pp** | `[+42.8, +75.2]` |
| codegemma-7b | 46.5% | 11.5% | **+35.0pp** | `[+19.7, +50.3]` |
| granite-code-8b | 20.5% | 11.0% | **+9.5pp** | `[+3.8, +15.2]` |

† **The frontier row is not the same measurement as the three below it, and
its rates are not comparable to theirs.** It ran **12 defect classes × 3
arms × 1 seed = 36 repairs**, not 600, and was **unconstrained on every
arm** — where the local families' Oxide and explicit arms were
grammar-constrained. Its `92%` figures are 11/12 and 11/12. The 12 classes
were chosen as the *hardest* at 7B: the first eight scored 23/23, so the
remaining four were picked as hardest-for-Rust and the other eight were
never run, on the grounds that confirming a ceiling with easier classes
carries near-zero information. What this row establishes is that **the
delta vanishes at frontier capability** — 0.0pp, arms identical class for
class. It is not a like-for-like rate comparison. Source and full method:
[`eval/results/ownership-probe-frontier/`](eval/results/ownership-probe-frontier/).

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

Adding builtin method syntax (SPEC §53) moved these numbers enormously —
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

### Ergonomic fixes, and what each was worth

Every one came from reading failing submissions rather than theorising, and
each shows the same dose-response — it helped exactly the models that had the
problem.

The two groups below are **measured on different instruments** and their
numbers must not be read across: repair deltas come from the ownership probe,
generation deltas from the constrained grid.

**Measured on repair (v0.2.2).** Oxide strict change on the probe corpus:

| Fix | mechanism removed | effect |
|---|---|---|
| **Method syntax** (§53) | `.clone()` → `OX0304`, 94→0 and 72→0 | **+42pp** (qwen), **+31pp** (codegemma), **−1.5pp** (granite, which never used it) |
| **`mut` accepted** (§54) | `let mut acc` glued into `let mutacc`, `OX0200` 81→10 (qwen) and 42→**0** (codegemma) | **+5.5pp** (qwen), **+3.0pp** (codegemma), **0** (granite) |

**Measured on generation (v0.3).** Three constrained campaigns, oxide arm,
200 first attempts per family per arm — qwen / codegemma / granite:

| Fix | mechanism removed | first-compile | first-pass |
|---|---|---|---|
| **`vec(...)` literal** (§55) | push-chain boilerplate; targeted counter 91→21, 69→13, 27→6 | **+4.5 / +3.5 / +2.0** | **+3.0 / +2.0 / +0.5** |
| **field assignment + `to_str`** (§56, §57 — landed together, inseparable) | the constrained decoder's `f.x == e` substitution, 18→0; a naming gap | +1.5 / 0 / 0 | +1.5 / 0 / 0 |

**One of the three did the work.** §55 took **10.0 of the 11.5 first-compile
points** and 5.5 of the 7.0 first-pass points, and its per-family effect
orders exactly as its measured demand did — 91 > 69 > 27 `vec` calls giving
+4.5 > +3.5 > +2.0. What is left is one family's 3 sessions of 200, and it is
probably not §56 either: the family with the *most* deformation to remove
(codegemma, 10 occurrences) moved 0.0, while the one that moved had the
fewest (qwen, 2).

Both were still worth shipping, for reasons that are not rate. §56 eliminated
a *measurement* artifact — the grammar-constrained decoder substituting `=`
for `==` in statement position, 18 occurrences to 0 — and its implementation
surfaced the one defect class found in this project that **rustc accepts**,
so the oracle never sees it. §57 provably could not have moved rates: of the
programs reaching for `to_str`, none compiled either before or after the alias
existed, so its largest possible effect on first-compile was 0.0pp. The wall
those programs hit is the parser, and a builtin alias acts downstream of it.

That is what a loop at diminishing returns looks like, and it is why the next
step is a fine-tune track rather than a fourth ergonomic fix. Full accounting:
[`eval/results/v03-synthesis/`](eval/results/v03-synthesis/).

The `mut` fix is the more instructive of the repair pair. Chasing `OX0200` — the largest remaining
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
- Anything about **writing** Black Oxide. These models cannot. Under
  constrained decoding at v0.3, first-attempt pass rates are
  **30.5 / 16.5 / 9.5%** for Black Oxide against **56.5 / 45 / 42%** for Rust
  (qwen2.5-coder-7b / codegemma-7b / granite-code-8b, 200 first attempts per
  family per arm; at v0.2.2 the Black Oxide side was 26 / 14.5 / 9%). That gap
  is pretraining exposure, not language design — and at frontier it closes
  entirely: on whole-program authorship a frontier model scored **20/20 in all
  three arms**, Rust and both Black Oxide dialects alike.

  *Superseding an earlier figure:* this README previously cited the 6a
  pilot's **2/20 against 20/20**. The Black Oxide side replicates — G0's
  unconstrained qwen first-compile is 10.0% — but the Rust side does not.
  20/20 was a 20-sample high against 56.5% measured over 200. The gap is
  real and large; it is not 10× wide.

  *Two corrections to how this bullet read before v0.3:* its denominator said
  600 first attempts per family per arm, which is **200** — 600 is per family,
  across the three arms. And it cited "a frontier model prefers Rust 100 to
  92", which is 12/12 against 11/12 from the **repair** probe rather than
  generation, and whose single failure that probe's own report records as a
  corpus defect, not a model failure. Neither figure supported a claim about
  writing; the 20/20 generation result above is what the frontier data
  actually says.

## How it was measured

Whole-program generation could not test the ownership claim at all. Across ~530
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
