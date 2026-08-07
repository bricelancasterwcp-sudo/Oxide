# Oxide

A Rust-like language with **implicit linear types**: ownership works like
Rust's, but there is no borrow syntax. The compiler infers moves, borrows, and
destruction points; types are fully inferred. Oxide transpiles to Rust, and
`rustc` is the oracle — any program Oxide accepts that `rustc` rejects is a
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

The thesis is that **LLMs write Oxide more reliably than Rust**, because the
compiler does the ownership bookkeeping instead of the programmer.

That claim is deliberately falsifiable, and this repository contains the
apparatus built to falsify it — plus the results, including the ones that
didn't go the thesis's way.

## Where the thesis actually stands

**Partially supported, with a capability window.** Ordered by how well each
subject performs the task at all:

| Subject | Oxide | explicit-Oxide | paired delta |
|---|---|---|---|
| Claude Opus 5 | 92% | 92% | **0.0pp** — ceiling, arms identical |
| qwen2.5-coder-7b | 26% | 16% | **+10.0pp**, 2-SE `[−1.7, +21.7]` |
| codegemma-7b | 17% | 3% | **+13.3pp**, interval spans zero |
| granite-code-8b | 8% | 2% | **+6.7pp** — both arms at the floor |

The comparison that matters is **Oxide vs explicit-Oxide** — a control dialect
with identical grammar, builtins, and diagnostics, where ownership is written
out by hand (`&` reads, declared parameter modes, mandatory `drop`). Both are
languages the model has never seen, taught only by a card of comparable
length. They differ in exactly one thing: whether ownership is implicit.

Across three independent model families, **20 of 26 non-tied class×model
comparisons favour implicit linearity** (two-sided exact sign test,
p = 0.0094). The direction replicates.

The magnitude does not. **No single family resolves it.** An earlier 3-seed
run put qwen at +18.3pp with an interval excluding zero; re-running at 10
seeds gave +10.0pp with an interval spanning it, so that "resolved" result was
an artifact of too few seeds and has been withdrawn. See
[`eval/results/ownership-probe-10seed/`](eval/results/ownership-probe-10seed/).
The other two families are still at 3 seeds and their numbers should be
treated as provisional for the same reason.

**Supported:** implicit linearity is an accessibility win, and it is real
across model families.

**Not supported:**

- *"Makes LLMs more reliable"* without qualification. At frontier the delta is
  exactly zero — a model that already reasons about ownership correctly gains
  nothing.
- Any specific magnitude, and no claim of statistical significance for any
  single subject. Every per-family interval that has been measured at
  adequate seed count includes zero.
- Anything about **writing** Oxide. These models cannot. A 7B model scores
  **2/20** first-compile writing Oxide from the card, against 20/20 for Rust.
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
  degenerate-fix rate — repairs that silence the error while changing what the
  program does. It runs **32/60** and **40/60** in the Oxide arms against
  **1/60** for Rust. On lenient alone the three arms are indistinguishable;
  strict separates them completely. Reporting lenient without strict inverts
  the conclusion.

## It found a real bug

`OX0403` (loop-carried move) named only the move site. In a loop the move and
the conflicting use are the same syntax, so the diagnostic pointed at one
location twice and never mentioned the use *after* the loop — the thing that
makes the move fatal. `rustc` names both ends.

A frontier model, given the old diagnostic, cloned inside the loop — which
`OX0403`'s own suggestion recommended — producing a program that compiles,
silences the error, and never accumulates. It repaired the Rust version
correctly. Fixing the diagnostic flipped that probe from fail to pass in both
Oxide dialects.

At 7B the same fix changes nothing — oxide strict is 25.5% before and after,
across 600 repairs each way. Causal at frontier, inert at 7B; both are
measured, and the fix stands on its own merits either way, since the old
diagnostic named one location twice and omitted the use that makes the move
fatal.

## Quick start

Requires Python 3.14 and a Rust toolchain. No third-party Python dependencies.

```bash
python3 -m venv .venv && .venv/bin/pip install pytest pytest-cov
.venv/bin/pytest tests/ -q                 # 1240 tests
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
```

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

Oxide is a research vehicle, not a usable language. It has no generics,
traits, closures, modules, tuples, indexing syntax, or sized integer types.
It is a strict subset of what it compiles to.

Every result here is one shot condition on a 20-class corpus. The primary
subject is measured at 10 seeds; the two replication families are still at 3
and should be re-run before their numbers are relied on. The reports in `eval/results/` state their own limits,
including which conclusions the data does *not* support and which numbers
sit at a floor or ceiling where the design has no resolution.

`eval/results/*/REPORT.md` is the place to start if you want the full picture
rather than the summary.

## License

MIT — see [LICENSE](LICENSE).
