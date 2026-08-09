# Black Oxide v0.3 — G0 generation-baseline taxonomy evidence

**Scope.** Constrained condition, first attempts only, `oxide` + `explicit` arms
(the `rust` arm is a control and emits rustc `E0xxx` codes, never `OX0xxx`).
Three families × 10 seeds × 20 tasks × 2 Black Oxide arms = **400 programs per family**.
Evidence is drawn from every failing program in the corpus, not only from the
15-file-per-code `samples/` selection; sample `.txt` paths are cited where the
selection happens to contain the cited program, otherwise the citation is the
run's `triples.jsonl` plus `task`/`arm`/`line`, which is the same record.

Base paths (abbreviated below as `C/<run>` and `S/`):

- `C/<run>` = `/home/brice/workspace/oxide/eval/results/g0-generation-baseline/constrained/<run>/triples.jsonl`
- `S/` = `/home/brice/workspace/oxide/eval/results/g0-generation-baseline/constrained/samples/`
- `U/<run>` = `/home/brice/workspace/oxide/eval/results/g0-generation-baseline/unconstrained/<run>/triples.jsonl`

Headline counts reproduce the brief exactly:

| code | qwen | codegemma | granite | programs affected (of 400/family) |
|---|---|---|---|---|
| OX0200 | 274 | 280 | 456 | 94 / 120 / 102 |
| OX0203 | 111 | 319 | 310 | 15 / 16 / 51 |
| OX0306 | 127 | 179 | 81 | 54 / 77 / 28 |
| OX0303 | 91 | 69 | 27 | 78 / 59 / 23 |
| OX0300 | 80 | 84 | 74 | 46 / 51 / 38 |

First-attempt outcomes for context (compiled / passed, of 200 per family-arm):
qwen oxide 60/52, qwen explicit 46/41; codegemma oxide 40/29, explicit 40/25;
granite oxide 62/18, explicit 40/18. Rust control: qwen 196/114, codegemma
169/90, granite 160/84.

---

## OX0200 — unknown name (1010 diagnostics)

Arm split: oxide 666, explicit 344. 980 are plain uses; only **30** carry the
`in assignment` suffix (`unknown identifier 'n' in assignment`), i.e. literal
Python-style "assign without `let`" is a small minority — codegemma 16,
granite 14, qwen 0.

### Sub-class table

| sub-class | n | qwen | codegemma | granite | share |
|---|---|---|---|---|---|
| D1 unbound variable (never bound anywhere) | 237 | 85 | 64 | 88 | 23.5% |
| B1 type name used as value / constructor | 182 | 38 | 32 | 112 | 18.0% |
| C foreign stdlib function | 175 | 29 | 67 | 79 | 17.3% |
| B2 undeclared struct/enum constructor | 114 | 31 | 16 | 67 | 11.3% |
| E2 foreign keyword/literal as identifier | 101 | 29 | 40 | 32 | 10.0% |
| A undefined helper fn (called, never defined) | 95 | 11 | 47 | 37 | 9.4% |
| D2 bound elsewhere (scope error) | 52 | 29 | 8 | 15 | 5.1% |
| E1 GBNF keyword+identifier gluing | 36 | 18 | 4 | 14 | 3.6% |
| D3 closure parameter | 18 | 4 | 2 | 12 | 1.8% |

**Dominant sub-class per family:** qwen D1-unbound-variable (85/274);
codegemma C-foreign-stdlib (67/280) with A-undefined-helper close behind (47);
granite B1-type-name-as-value (112/456).

### B — capitalized names (352 diagnostics total across B1+B2), refined

Re-split by whether the name is declared in the same program and whether it is
called:

| refined bucket | n | qwen | cg | granite | top names |
|---|---|---|---|---|---|
| B-a declared `struct` constructed **positionally** | 106 | 32 | 2 | 72 | List=45, Fibonacci=11, Int=8, Item=7, Sequence=6 |
| B-e capitalized name in value position, never declared | 82 | 30 | 18 | 34 | False=31, True=18, CSRF=4, MATCH=3 |
| B-c builtin/foreign type name in value position | 69 | 8 | 12 | 49 | Box=37, String=12, Int=6, Vec=5 |
| B-b builtin/foreign type name **called** as constructor | 53 | 21 | 20 | 12 | Vec=21, Str=11, String=8, Tuple=5 |
| B-d capitalized ctor never declared anywhere | 42 | 4 | 13 | 25 | Shape=9, Rectangle=8, TODO=7, Triangle=4 |

**B-a — struct declared with named fields, constructed positionally.** The
model declares `struct S { a: T, b: T }` and then writes `S(a, b)`.

```
struct State {
    value: Int,
    steps: Int,
}

fn transform(state: State) -> State {
    let new_value = if state.value % 2 == 0 { state.value / 2 } else { state.value * 3 + 1 }
    State(value, state.steps + 1)
}
```
`S/resolve/OX0200/g0c-qwen7b-0shot-s1.t02.oxide.txt` L1–9
(diagnostics: `unknown identifier 'State'`, `unknown identifier 'value'`)

```
fn next(seq: Sequence) -> Sequence {
    Sequence(seq.term2, seq.term1 + seq.term2)
}
```
`C/g0c-granite8b-0shot-s9` t05.oxide L6–8

*Reaching for:* tuple-struct / positional constructor syntax, and a
constructor that is a first-class callable rather than a `{ field: ... }`
literal. Note the same program still writes `state.value` for reads, so this
is specifically a construction-site habit.

**B-b/B-c — builtin type names treated as values.** `Vec()` for "empty vector",
`String(...)`/`String.new()` for string construction, `Box` for indirection.

```
    let mut kept = Vec()
```
`C/g0c-qwen7b-0shot-s9` t10.oxide L5

```
fn vec() -> Vec<Int> {
    Vec()
}
```
`C/g0c-granite8b-0shot-s6` t08.oxide L74–76

```
fn palindrome(word: &String) -> String {
    let mut reversed = String.new()
```
`C/g0c-codegemma7b-0shot-s2` t13.explicit L1–2

`Box` (37 occurrences, granite 38 of 39 across all forms) appears exclusively
in recursive-type declarations, e.g. `Cons(Int, Box<Vec>)`
(`C/g0c-codegemma7b-0shot-s9` t19.oxide).

*Reaching for:* a `Vec::new()`-shaped empty-collection constructor named after
the type; a `String` type distinct from `Str`; heap indirection for recursive
data.

**B-e — Python booleans.** `False`=31, `True`=18 (qwen 26, codegemma 17,
granite 6 combined).

```
    if n % i == 0 {
        return False
    }
```
`C/g0c-granite8b-0shot-s9` t04.oxide L4–6

*Reaching for:* Python's capitalized boolean literals.

**B-d — enum-shaped shape modelling that was never declared.**

```
    let shapes = vec(Shape(Rectangle(3.0, 4.0)), Shape(Triangle(3.0, 4.0, 5.0)), Shape(Rectangle(2.0, 5.0)))
```
`C/g0c-codegemma7b-0shot-s2` t18.explicit L12 — `Shape`, `Rectangle`,
`Triangle` all used as constructors, none declared.

*Reaching for:* a sum type for heterogeneous collections, sketched at the use
site before (or instead of) declaring it.

### C — foreign stdlib functions (175)

Top names: `println`=46 (cg 32, gr 13, qwen 1), `pop`=17, `format`=14,
`print_int`=14, `add`=11, `contains`=8, `set`=8, `next`=7, `reduce`=6,
`str`=6, `list`=6, `extend`=3, `slice`=3, `sum`=3, `max`=3, `sqrt`=1,
`reverse`=1, `swap`, `fold`, `vec_new`, `char`, `truncate`.

```
fn print_str(s: Str) {
    println(s)
}
```
`C/g0c-granite8b-0shot-s6` t08.oxide L102–104

```
        if !contains(distinct, c) {
            distinct = push(distinct, c)
```
`C/g0c-qwen7b-0shot-s1` t14.oxide L10–11

```
            v = pop(v)?
```
`C/g0c-codegemma7b-0shot-s2` t11.oxide L5

```
    let sum = v.iter().fold(0, add)
```
`C/g0c-codegemma7b-0shot-s2` t06.oxide L13

*Reaching for, in order of volume:* a line-printing primitive that appends a
newline (`println`); a stack pop (`pop`); string interpolation (`format`);
typed print variants (`print_int`); membership test (`contains`); index
assignment (`set`); a fold/reduce with a named binary function (`add`,
`reduce`, `fold`, `max`). The `sum`/`max`/`min` demand is real but small
(3+3+1); the far larger demand is `println` and `pop`.

### A — undefined helper functions (95)

Called but never defined. Top: `vec_str`=10, `sum_big`=5, `bchar`=3,
`vec_int`=3, `loop_start`/`loop_end`=3 each, `vec_push`=3,
`str_from_chars`=3, `perimeter`=3, `first_big`=2, `if_then_else`=2,
`truncate`, `print_cost`, `cmp_int`, `is_positive`.

```
fn main() {
    let values = vec(3, 8, -2, 12, 7)
    let positives = values.iter().filter(is_positive).count()
    let largest = values.iter().copied().max_by(cmp_int)
```
`S/resolve/OX0200/g0c-codegemma7b-0shot-s1.t08.oxide.txt` L1–4

```
        positive = positive + if_then_else(n > 0, 1, 0)
        largest = if_then_else(n > largest, n, largest)
```
`S/resolve/OX0200/g0c-granite8b-0shot-s1.t08.oxide.txt` L6–7

```
        v = push(vec(), truncate(v, v.len() - 1))
```
`S/resolve/OX0200/g0c-qwen7b-0shot-s1.t09.oxide.txt` L5

Note `sum_big`=5 and `first_big`=2 are **card examples called as if they were
stdlib** — see cross-cutting §C2.

*Reaching for:* named-function predicates/comparators to pass to iterator
combinators; a ternary/select expression (`if_then_else`); a Vec truncate/drop-
last; helpers the model planned top-down and then never emitted.

### D1/D2/D3 — unbound variables (307 combined)

D1 top names: `x`=39, `n`=38, `acc`=12, `node`=11, `num`=8, `item`=8,
`dividend`=6, `i`=6. A large share are **not** implicit binding but downstream
fallout from a *different* failure in the same line:

```
fn count(mutn: Int) -> Int {
    let mut steps = 0
    while n != 1 {
        if n % 2 == 0 {
            n = n / 2
```
`S/resolve/OX0200/g0c-codegemma7b-0shot-s1.t02.explicit.txt` L1–5 — all six
`OX0200 'n'` diagnostics stem from `mut n` in the parameter list being lexed
as one identifier `mutn` (see E1 / cross-cutting §C1).

Genuine implicit-binding cases exist and look like this:

```
fn next(f: Fibonacci) -> Fibonacci {
    Fibonacci(a + f.b, f.a)
}
```
`C/g0c-qwen7b-0shot-s9` t05.oxide L3–5 — `a` was never bound; the model wrote
a struct-update expression assuming field names are in scope.

D3 (18) is closure-parameter scope — the model writes a lambda in a syntax the
grammar mangles, so the parameters never bind:

```
    let total = fold(r2.values, 0, fun(acc, n) == acc + n * n)
```
`C/g0c-granite8b-0shot-s6` t01.oxide L6 — `fun`, `acc`, `n` all unknown.

D2 (52, qwen-skewed at 29) is a real scoping error: the name **is** bound in
the program but not in the referencing scope (`item`=11, `lst`=9, `i`=7).

*Reaching for:* Python-style assign-creates-binding (small); anonymous
functions with inline parameters (D3); field names in scope inside a
constructor expression.

### E1 — GBNF keyword+identifier gluing (36)

`letSome`=21, `fnacc`=3, `mutv`=2, `whilei`, `letx`, `returnOk`, `breakn`,
`letOk`, `ifx`, `returnSome`, `fnitem`, `returnErr`, `elsex`.

```
            if letSome(m) == max {
                if x > m {
```
`C/g0c-qwen7b-0shot-s9` t08.explicit L8–9

```
    let sum = whilei < len(v) && (letx == 3) * 3 * i + v.get(i)?
```
`C/g0c-granite8b-0shot-s6` t07.oxide L7

*Reaching for:* `if let Some(m) = max { ... }` / `while let Some(x) = ...`.
Under the constrained grammar there is no `if let` production, so the tokens
`let` and `Some` fuse into a single identifier and the call-shape survives as
`letSome(m) == max`. Total `let|while|if` + `Some|Ok|None|Err` fusions across
the constrained corpus: qwen 16, granite 4, codegemma 3.

---

## OX0203 — duplicate top-level name (740 diagnostics, 82 programs)

Programs affected: granite 51, codegemma 16, qwen 15. Diagnostic counts are
heavily inflated by a handful of degenerate programs, so both a
**program-level** and a **diagnostic-weighted** view are given.

### What is duplicated (by name)

`main`=82, `perimeter`=47, `print`=42, `area`=38, `Rectangle`=37,
`print_str`=33, `vec`=27, `cost`=24, `Item`=24, `sort_by_key`=18,
`unwrap`=18, `get`=17, `parse_int`=17, `push`=16, `int_to_str`=14,
`Triangle`=13, `len`=12, `clone`=10, `range`=8, `chars`=8, `trunc`=8,
`str_len`=7, `Rect`=7, `Some`=5, `None`=5, `to_float`=5, `println`=5.

By name category:

| category | diags | qwen | cg | granite |
|---|---|---|---|---|
| redefines a builtin **function** (`print`, `push`, `vec`, `get`, `len`, …) | 228 | 64 | 60 | 104 |
| duplicate user helper fn | 199 | 41 | 67 | 91 |
| duplicate struct/enum | 182 | 2 | 132 | 48 |
| duplicate `main` | 82 | 3 | 28 | 51 |
| struct/enum clashes with a builtin **type** name | 49 | 1 | 32 | 16 |

### Mechanism (program-level, one tag per program)

| mechanism | programs | qwen | cg | granite | diags | diag share |
|---|---|---|---|---|---|---|
| S — reimplements the builtin stdlib as user functions | 44 | 9 | 8 | 27 | 260 | 35.1% |
| R — verbatim self-repetition of the whole program | 16 | 0 | 7 | 9 | 269 | 36.4% |
| O — type-based function overloading | 15 | 3 | 2 | 10 | 22 | 3.0% |
| X — two independent complete solutions in one file | 14 | 3 | 1 | 10 | 185 | 25.0% |
| C — card-example echo | 5 | 0 | 0 | 5 | 4 | 0.5% |

**Dominant sub-class per family:** qwen S-stdlib-reimplementation (103 of 111
diags); codegemma R-verbatim-repetition (139 of 319); granite
S-stdlib-reimplementation (109 of 310, with R at 130 close behind — granite
splits between the two).

**S — reimplementing the stdlib.** The model writes its solution, then appends
definitions for every builtin it used.

```
fn print(n: Int) {
    let s = int_to_str(n)
    print_str(s)
}

fn len(v: Vec<Int>) -> Int {
    v.len()
}

fn push(v: Vec<Int>, x: Int) -> Vec<Int> {
    let mut out = v
    out.push(x)
    out
}

fn vec() -> Vec<Int> {
    Vec()
}

struct Option {
    value: Int,
}
```
`C/g0c-granite8b-0shot-s6` t08.oxide L57–80 (117-line program; 13 OX0203)

The single worst program is `S/resolve/OX0203/g0c-qwen7b-0shot-s1.t20.oxide.txt`
with **91** OX0203 diagnostics — `print_str`×19, `print`×18, `vec`×18.

*Reaching for:* self-sufficiency. The model does not believe the builtin list
is complete/available and provides its own `print`, `len`, `push`, `vec`,
`get`, `clone`, `parse_int`, `int_to_str`, `print_str`, plus `Option`/`Some`/
`None` as user structs.

**O — type-based overloading.** One function per receiver type, same name.

```
fn perimeter(r: Rectangle) -> Int {
    2 * (r.width + r.height)
}

fn perimeter(t: Triangle) -> Int {
    t.side1 + t.side2 + t.side3
}
```
`C/g0c-granite8b-0shot-s6` t18.oxide L5–11

```
fn cost(p: Pen) -> Int {
    0 + p.price * p.quantity
}

fn cost(p: Pad) -> Int {
    cost(p)
}

fn cost(p: Ink) -> Int {
    cost(p)
}
```
`C/g0c-granite8b-0shot-s9` t20.oxide L7–17

```
fn perimeter(r: Rectangle) -> Int { 2 * (r.width + r.height) }
fn perimeter(t: Triangle) -> Int { t.sides.iter().sum() }
fn perimeter(p: Point) -> Int { ... }
```
`S/resolve/OX0203/g0c-qwen7b-0shot-s1.t18.oxide.txt` L7–18

*Reaching for:* ad-hoc polymorphism — one operation name dispatched on
argument type, i.e. the thing `impl` blocks / traits / overloads provide. This
is the same intent that OX0306's `p.area()` would express; when the card's
"no user methods" rule is obeyed, the demand resurfaces here as overloading.

**R — verbatim self-repetition.** The model emits a correct-looking program and
then repeats it 3–8 times.

```
fn area(rect: &Rectangle) -> Int { rect.width * rect.height }
fn perimeter(rect: &Rectangle) -> Int { 2 * (rect.width + rect.height) }
fn main() { let rect = Rectangle { width: 7, height: 4 } ... }
struct Rectangle { width: Int, height: Int }
   ← the four items above repeat 6× in the same file
```
`S/resolve/OX0203/g0c-codegemma7b-0shot-s2.t19.explicit.txt` (19 OX0203; head
block occurs 6×). Also `S/resolve/OX0203/g0c-codegemma7b-0shot-s4.t20.oxide.txt`
(48 OX0203, `Item`×22, `cost`×21).

*Reaching for:* nothing linguistic — this is a stop-condition/degeneracy
artifact. It is concentrated in t19/t20 (see cross-cutting §C4).

**X — two independent complete solutions.** Distinct from R: the model solves
the task, then solves it again differently (or solves a *different* task).

```
fn main() {
    print_str(sum_of_primes_before(2000).to_str())
}
```
`C/g0c-granite8b-0shot-s6` t06.oxide L36–38 — a second `main` after a complete
perfect-numbers solution.

```
fn sum_big(v: &Vec<Int>, limit: Int) -> Int { ... }
fn main() { let nums = push(push(vec(), 5), 40) ... drop bigger }
fn square(x: Int) -> Int { x * x }
fn sum_squares(n: Int) -> Int { ... }
fn main() { print(sum_squares(10)) }
```
`C/g0c-granite8b-0shot-s5` t01.explicit — the first `main` is the **explicit
card's example program**, emitted verbatim before the actual answer.

Also in X: `C/g0c-codegemma7b-0shot-s9` t19.oxide declares the entire builtin
type universe as user enums (`enum Result`, `enum Option`, `enum Vec`,
`enum Str`, `enum Int`, `enum Bool`, `enum Char`, `enum Some`, `enum None`,
`enum Ok`, `enum Err`, repeated) — 4 of the 49 "clashes with a built-in type
name" diagnostics come from this one program.

---

## OX0306 — receiver syntax on a non-builtin (387 diagnostics)

Arm split: explicit 267, oxide 120. The diagnostic text names only the receiver
type; the method name was recovered from the source span.

### Invented method names (top 30)

| method | n | qwen | cg | granite | receiver kinds |
|---|---|---|---|---|---|
| `to_int` | 76 | 20 | 55 | 1 | numeric literal 65, chain 10 |
| `unwrap` | 63 | 16 | 23 | 24 | chain 58, variable 5 |
| `to_string` | 57 | 8 | 29 | 20 | string literal 40, chain 8, var 8 |
| `unwrap_or` | 28 | 11 | 17 | 0 | chain 28 |
| `rev` | 26 | 9 | 0 | 17 | string literal 16, chain 10 |
| `to` | 19 | 2 | 12 | 5 | numeric literal 19 |
| `nth` | 17 | 2 | 13 | 2 | chain 14, string literal 3 |
| `set` | 12 | 12 | 0 | 0 | variable 12 |
| `iter` | 9 | 4 | 5 | 0 | variable 6, chain 3 |
| `to_str` | 8 | 3 | 1 | 4 | mixed |
| `powf` | 6 | 6 | 0 | 0 | chain 6 |
| `floor` | 6 | 6 | 0 | 0 | chain 6 |
| `upto` | 5 | 0 | 5 | 0 | numeric literal 5 |
| `sqrt` | 5 | 2 | 3 | 0 | chain 5 |
| `is_none` | 5 | 3 | 0 | 2 | variable 5 |
| `contains` | 4 | 1 | 2 | 1 | variable 4 |
| `is_some` | 4 | 0 | 3 | 1 | mixed |
| `next` | 3 | 0 | 3 | 0 | string literal 3 |
| `eq` | 3 | 3 | 0 | 0 | string literal 3 |

Tail (1–2 each): `step_to`, `veclen`, `up_to`, `max`, `abs`, `insert`,
`collect_vec`, `collectVec`, `step`, `to_int_unchecked`, `contains_str`,
**`area`**, **`perimeter`**, `is_empty`, `pop`, `collect`, `into_iter`,
`into`, `rem_euclid`, `format`, `step_by`, `split_whitespace`, `charAt`.

### By intent group

| intent group | n | share | qwen | cg | granite |
|---|---|---|---|---|---|
| conversion (`to_int`, `to_string`, `to_str`, `to_float`, `into`, `format`) | 144 | 37.2% | 32 | 87 | 25 |
| Option/Result methods (`unwrap`, `unwrap_or`, `is_none`, `is_some`, `expect`) | 100 | 25.8% | 30 | 43 | 27 |
| iterator methods (`iter`, `rev`, `nth`, `next`, `collect`, `map`, `position`, …) | 61 | 15.8% | 20 | 22 | 19 |
| Int range builders (`to`, `upto`, `up_to`, `step_to`, `step`, `step_by`) | 30 | 7.8% | 3 | 18 | 9 |
| Vec mutation/query (`set`, `insert`, `push`, `pop`, `contains`, `is_empty`, `len`) | 23 | 5.9% | 18 | 4 | 1 |
| float math (`floor`, `sqrt`, `powf`, `abs`, `rem_euclid`, `max`) | 22 | 5.7% | 17 | 5 | 0 |
| string equality/indexing (`eq`, `charAt`) | 5 | 1.3% | 5 | 0 | 0 |
| **user-struct methods** (`area`, `perimeter`) | **2** | **0.5%** | 2 | 0 | 0 |

**Dominant sub-class per family:** qwen conversion (32) narrowly over
Option-methods (30) and Vec-mutation (18); codegemma conversion (87);
granite Option-methods (27) narrowly over conversion (25).

The headline finding is that **user-defined struct methods are almost absent**
(2 of 387, 0.5%). The card's explicit warning (`p.area()` is an error) is
obeyed. What models actually invent are **Rust/Ruby/Python stdlib methods on
builtin types**.

### Snippets per sub-class

**Conversion on numeric/string literals.**
```
fn main() {
    let rect = (7.0.to_int()).to_int()
```
`S/types/OX0306/g0c-qwen7b-0shot-s2.t19.explicit.txt` L9–10

```
    let str = "mississippi".to_string()
```
`C/g0c-codegemma7b-0shot-s2` t14.explicit L2

```
fn main() {
    let level = "level".to_string() == "elvel".to_string() && "level".to_string() == "level".to_string().chars().rev().collect() && ...
```
`C/g0c-granite8b-0shot-s7` t13.explicit L1–2

*Reaching for:* `Int::to_string()` / `&str → String` conversion methods, and a
`Str` type constructed from a literal rather than being one.

**Option/Result methods.**
```
fn sum(v: &Vec<Int>, i: Int) -> Int {
    v.get(i - 1).unwrap() + v.get(i - 2).unwrap()
}
```
`C/g0c-codegemma7b-0shot-s8` t05.explicit L1–3

```
    let list = vec(10, 20, 30)
    print(get(list, 1).unwrap_or(-1))
    print(get(list, 5).unwrap_or(-1))
```
`C/g0c-codegemma7b-0shot-s8` t17.oxide L2–4

```
        reversed = push(reversed, get(list, len(list) - 1 - i).unwrap())
```
`S/types/OX0306/g0c-granite8b-0shot-s3.t09.explicit.txt` L4

*Reaching for:* a non-`?` way to discharge the `Option` that `get` returns —
`unwrap()` for "I know it's there" and `unwrap_or(d)` for the bounded-lookup
task (t17). 58 of 63 `unwrap` receivers are `get(...)`/`.get(...)` chains, so
this is specifically about indexing, not Options in general.

**Int range builders.**
```
fn main() {
    let total = 0
    for x in 0.to(9) {
        total = total + x * x
    }
```
`S/types/OX0306/g0c-codegemma7b-0shot-s1.t01.explicit.txt` L1–4

```
    for x in 2.upto(n - 1) {
```
`C/g0c-codegemma7b-0shot-s8` t04.oxide L5

*Reaching for:* `0..=9` / Ruby's `0.upto(9)` — a range built from the integer
itself instead of via `range(a, b)`.

**Iterator chains.**
```
fn print_in_reverse(v: &Vec<Int>) {
    for x in v.iter().rev() {
```
`C/g0c-codegemma7b-0shot-s2` t09.explicit L1–2

```
    while i < j {
        if s.chars().nth(i).unwrap() != s.chars().nth(j).unwrap() {
```
`C/g0c-qwen7b-0shot-s9` t13.oxide L5–6

```
fn main() {
    let s = "stack".chars().rev().collect().concat().print()
}
```
`C/g0c-qwen7b-0shot-s9` t12.oxide L1–3

*Reaching for:* reverse iteration (`rev`) and positional character access
(`nth`) — Black Oxide has neither; `chars()` returns a `Vec<Str>` and there is no
reverse.

**Vec index-assignment (qwen-specific, `set`).**
```
        let temp = v.get(min_index).unwrap()
        v.set(min_index, v.get(i).unwrap())
        v.set(i, temp)
```
`C/g0c-qwen7b-0shot-s9` t11.explicit L10–12

*Reaching for:* `v[i] = x`. Corpus-wide `.set(` attempts: qwen 37, granite 6,
codegemma 5.

**Float math (qwen-specific).**
```
    let d1 = ((p2.x - p1.x).to_float()).powf(2.0) + ((p2.y - p1.y).to_float()).powf(2.0)
```
`C/g0c-qwen7b-0shot-s4` t18.oxide L4

```
    let limit = trunc(to_float(n).sqrt())
```
`C/g0c-qwen7b-0shot-s6` t06.oxide L3

*Reaching for:* `sqrt`/`pow`/`floor`/`abs` — no numeric library exists.

**The two user-method cases.**
```
    let rect = (7.0.to_float()).trunc() * 4.0.to_float().trunc().to_int()
    print(rect.area())
    print(rect.perimeter())
```
`S/types/OX0306/g0c-qwen7b-0shot-s1.t19.explicit.txt` L10–12

A closely related case lands in OX0303 instead, where the model defines free
functions and then calls them UFCS-style on a struct whose fields share the
names:
```
fn print_perimeter(rectangle: &Rectangle) {
    let perimeter = rectangle.width() * 2 + rectangle.height() * 2
...
fn width(rectangle: &Rectangle) -> Int { rectangle.width }
fn height(rectangle: &Rectangle) -> Int { rectangle.height }
```
`S/types/OX0303/g0c-granite8b-0shot-s2.t18.explicit.txt` L1–17

Total `x.f()` where `fn f` **is** defined top-level in the same program:
qwen 2, granite 2, codegemma 0 (4 across the corpus).

---

## OX0303 — not callable / wrong arity (187 diagnostics)

Arm split: oxide 93, explicit 94.

| sub-class | n | qwen | cg | granite | share |
|---|---|---|---|---|---|
| V — `vec(a, b, c, …)` used as a variadic list literal | 148 | 69 | 59 | 20 | 79.1% |
| N — `not callable` | 14 | 7 | 2 | 5 | 7.5% |
| A — `range(a, b, step)` 3-arg | 7 | 5 | 2 | 0 | 3.7% |
| A — `push(v, a, b)` 3-arg | 5 | 5 | 0 | 0 | 2.7% |
| A — `print(fmt, x)` 2-arg | 5 | 1 | 4 | 0 | 2.7% |
| A — other (`concat` 1/3-arg, `chars` 2, `len` 2, `to_float` 2) | 8 | 4 | 2 | 2 | 4.3% |

**Dominant sub-class per family:** V-vec-as-variadic-literal for all three
(qwen 69/91, codegemma 59/69, granite 20/27).

**V — `vec()` as a list literal.** Arities seen: 2 (6×), 3 (56×), 5 (39×),
6 (45×), 8 (1×), 10 (1×) — exactly matching the task list lengths.

```
fn main() {
    let values = vec(3, 8, -2, 12, 7)
```
`C/g0c-codegemma7b-0shot-s2` t08.oxide L1–2

```
fn main() {
    let nums = vec(4, 1, 7, 3, 9, 2)
```
`S/types/OX0303/g0c-codegemma7b-0shot-s1.t10.oxide.txt` L1–2

```
fn main() {
    let list = vec(4, 1, 7, 3, 9, 2)
```
`C/g0c-granite8b-0shot-s6` t10.explicit L11–12

*Reaching for:* `vec![1, 2, 3]` / `[1, 2, 3]`. The card teaches
`push(push(vec(), 3), 42)`; the models keep the *name* `vec` and give it the
*arity* of a literal. This is the single largest concrete semantic demand in
the whole OX0303 bucket.

**N — `not callable`.** Two distinct causes:

(a) *indexing written as a call* (the grammar has no `[]`):
```
        for j in i + 1 < len(&sorted) {
            if sorted(j) < sorted(min) {
                min = j
```
`C/g0c-qwen7b-0shot-s3` t11.explicit L5–7 (`not callable: Vec<Int>`, 6×)

(b) *the builtin `vec` shadowed by a parameter or local of the same name*:
```
fn reverse(vec: &Vec<Int>) -> Vec<Int> {
    let result = vec()
```
`C/g0c-granite8b-0shot-s5` t09.explicit L1–2 (`not callable: Vec<Int>`)

Also one pure-syntax case, `let rec = (7)(4)`
(`C/g0c-qwen7b-0shot-s6` t19.explicit L10) — juxtaposition read as application.

**A — arity errors.** These are foreign call signatures:

```
    let nums = push(push(push(push(vec(), 1), 2), 3), 4, 5)
```
`S/types/OX0303/g0c-qwen7b-0shot-s1.t09.explicit.txt` L2 — the model runs out
of nesting patience and appends two values in one `push`.

```
    for x in range(len(nums), 0, -1) {
```
`C/g0c-codegemma7b-0shot-s2` t09.oxide L3 — Python's 3-arg `range` with a
negative step, i.e. reverse iteration.

```
        print("{}\n", x)
```
`C/g0c-codegemma7b-0shot-s7` t10.oxide L6 — `println!`-style format+arg.

*Reaching for:* variadic push, a stepped/descending range, and formatted print.

---

## OX0300 — operand type mismatch (238 diagnostics)

Arm split: oxide 130, explicit 108.

### Raw type pairs

`Int vs Float` 22, `Int vs Vec<?>` 21, `Bool vs Unit` 20, `Unit vs Option<?>` 18,
`Vec<Error> vs Unit` 18, `Vec<Int> vs Unit` 15, `Str vs Vec<?>` 15,
`Bool vs Vec<?>` 14, `Float vs Int` 12, `Option<Int> vs Int` 11,
`Int vs Str` 10, `Unit vs Int` 9, `Vec<Error> vs Int` 6, `Int vs Option<Int>` 4,
`Triangle vs Rectangle` 2, plus a 22-item tail. (`Error` is the sema's
error-type placeholder, so `Vec<Error>` means "a Vec whose element type already
failed".)

### Grouped by colliding pair

| pair group | n | qwen | cg | granite |
|---|---|---|---|---|
| Unit vs value | 90 | 36 | 29 | 25 |
| Vec vs scalar | 51 | 15 | 23 | 13 |
| Int vs Float | 34 | 9 | 14 | 11 |
| Str vs Vec | 23 | 7 | 5 | 11 |
| Option/Result vs payload | 22 | 5 | 8 | 9 |
| Str vs Int | 11 | 6 | 5 | 0 |
| Bool vs other | 4 | 2 | 0 | 2 |
| struct vs struct | 2 | 0 | 0 | 2 |

### Grouped by mechanism (what the model actually wrote)

| mechanism | n | qwen | cg | granite |
|---|---|---|---|---|
| R3 `if` without `else` used as a statement, branch yields a value | 44 | 15 | 12 | 17 |
| M6 Int/Float mixing | 30 | 9 | 14 | 7 |
| R2 Option used as a bare value (`get` result) | 31 | 8 | 10 | 13 |
| M1 `for x in <comparison>` (range-syntax substitute) | 20 | 7 | 1 | 12 |
| MA scalar accumulator pushed into | 19 | 6 | 8 | 5 |
| M2 `for x in <Int/Str>` (range substitute) | 17 | 3 | 13 | 1 |
| R1 `?` used in a function returning Unit (usually `main`) | 11 | 4 | 6 | 1 |
| R7 Str coercion expected (`print_str(Int)`, `concat(Int, …)`) | 11 | 6 | 0 | 5 |
| M7 Str treated as an indexable/`len`-able sequence | 9 | 0 | 8 | 1 |
| R4 `match` used as a statement, arm yields a value | 9 | 7 | 2 | 0 |
| M4 `push(...)` written as an in-place mutation statement | 8 | 3 | 5 | 0 |
| R5 assignment written as `==` | 4 | 3 | 0 | 1 |
| other | 25 | | | |

**Dominant sub-class per family:** qwen R3-if-without-else (15) with
R2-Option-as-value (8) second; codegemma M6-Int/Float and M2-for-over-Int
(14/13) leading; granite R3-if-without-else (17) with R2 (13) second.

### Snippets

**R3 — `if` without `else` as a statement.** The branch's trailing expression
is a value, so the `if` expression's type collides with `Unit`.
```
    for x in nums {
        if x > 3 {
            push(out, x)
        }
    }
```
`C/g0c-codegemma7b-0shot-s2` t10.oxide L4–7 (`Vec<Error> vs Unit`)

```
        for i in range(2, 100) {
            if is_prime(i) {
                primes.primes == push(primes.primes, i)
```
`C/g0c-qwen7b-0shot-s1` t04.oxide L24–26 (`Bool vs Unit`)

*Reaching for:* statement-vs-expression separation — an `if` that is allowed to
be an effectful statement without an `else` and without its branch value being
typed against `Unit`.

**M1/M2 — `for` over something that is not a Vec.** The range-syntax substitute.
```
fn fibonacci(n: Int) -> Int {
    let a = 1
    let b = 1
    for _ in 0 < n {
```
`S/types/OX0300/g0c-qwen7b-0shot-s1.t05.explicit.txt` L1–4 (`Bool vs Vec<?>`)

```
    for _ in 0 <= 19 {
```
`C/g0c-granite8b-0shot-s6` t05.explicit L5

```
    for i in 0 {
```
`C/g0c-qwen7b-0shot-s6` t20.explicit L3 (`Int vs Vec<?>`)

```
    for i in 2.to_int().to_float().sqrt().trunc() + 1.to_float().to_int() {
```
`C/g0c-codegemma7b-0shot-s10` t04.explicit L5

*Reaching for:* `for i in 0..n`. With `..` unavailable under the grammar, the
loop header degrades to a comparison or a bare integer — the shape of a
C/Python bounded loop with no range constructor.

**MA — scalar accumulator pushed into.**
```
fn sum_squares() -> Int {
    let mut acc = 0
    for x in range(0, 10) {
        acc = push(acc, x * x)
    }
    acc
}
```
`S/types/OX0300/g0c-codegemma7b-0shot-s1.t01.oxide.txt` L1–7

*Reaching for:* a single accumulate idiom that works for both "sum into an Int"
and "collect into a Vec" — the card's `acc = push(acc, item)` idiom is applied
to a numeric accumulator.

**R2 — `get` result used as a bare element.**
```
    for i in 0.len(prices) {
        sum = sum + prices.get(i) * quantities.get(i)
```
`C/g0c-codegemma7b-0shot-s1` t20.oxide L3–4 (`Int vs Option<Int>`)

```
    let x1 = get(&list, pos1)
    res = push(res, x1)
```
`S/types/OX0300/g0c-granite8b-0shot-s2.t17.explicit.txt` L3–4

```
        if index >= 0 && index < len(&list) {
            return get(&list, index)
```
`C/g0c-granite8b-0shot-s6` t17.explicit L2–3 (`Option<Int> vs Int`)

*Reaching for:* indexing that returns the element. Note the last case: the
model **already bounds-checked**, so it expects the check to discharge the
`Option`.

**M6 — Int/Float mixing.**
```
    let x = 100
    print(trunc(x / 5))
```
`C/g0c-codegemma7b-0shot-s9` t16.oxide L2–3 — `trunc` applied to an Int
division; the model treats `trunc` as "integer division result" rather than
Float→Int.

```
    let rect = (7.0.to_float()).trunc() * 4.0.to_float().trunc().to_int()
```
`S/types/OX0300/g0c-qwen7b-0shot-s1.t19.explicit.txt` L10 — Float literals for
integer dimensions, then a conversion cascade.

*Reaching for:* implicit numeric promotion, or at least a `/` that means
whole-number division on Ints without a Float detour.

**M7 — Str as an indexable sequence.**
```
    let mut start = 0
    let mut end = text.len() - 1
    while start < end {
```
`C/g0c-codegemma7b-0shot-s8` t13.explicit L2–4 (`Str vs Vec<?>`)

```
        if s.get(i) != s.get(len - i - 1) {
```
`C/g0c-qwen7b-0shot-s3` t13.oxide L4

*Reaching for:* `len`/`get` on `Str` directly (Black Oxide requires `str_len` and
`chars`).

**R7 — Str coercion.**
```
        for x in &sorted {
            print_str(&x)
```
`C/g0c-qwen7b-0shot-s6` t11.explicit L17–18 (`Int vs Str`)

```
    print_str(str_len(to_str(n)) + " is " + to_str(n) + "\n")
```
`C/g0c-granite8b-0shot-s6` t19.explicit L22

Corpus-wide `Str + Str` concatenation attempts (lines): granite 68, codegemma
10, qwen 6.

*Reaching for:* `+` for string concatenation, and a `print` that stringifies
anything.

**R5 — assignment written as `==`.**
```
            s.values == push(s.values, next)
```
`C/g0c-granite8b-0shot-s6` t05.oxide L10

```
    multiples.values == push(multiples.values, i * 3)
```
`C/g0c-qwen7b-0shot-s10` t07.oxide L14

```
        p.a == temp
```
`C/g0c-qwen7b-0shot-s5` t03.oxide L7

*Reaching for:* **assignment to a field / lvalue path** (`s.values = …`). Black Oxide
assignment targets a bare name only, so under constrained decoding `=` is
unavailable after a field path and the decoder settles on `==`, producing a
Bool where Unit is expected. Small in count (4) but unambiguous in intent, and
the same collapse appears in `sorted(i) == temp` (index-assignment,
`C/g0c-qwen7b-0shot-s3` t11.explicit L12).

**R1 — `?` in a Unit-returning function.**
```
fn main() {
    let nums = vec(1, 2, 3, 4, 5)
    for x in range(len(nums), 0, -1) {
        print(get(nums, x)?)
```
`C/g0c-codegemma7b-0shot-s2` t09.oxide L1–4 — the diagnostic anchors on
`fn main()` (`Unit vs Option<?>`).

*Reaching for:* `?` as a local "unwrap or die" rather than an early-return
operator constrained by the enclosing return type.

---

## Unconstrained demand check

Measured over the **1200 non-`rust` unconstrained first attempts** (400 per
family; the `rust` arm is excluded because Rust syntax there is correct by
construction and would contaminate every count).

Raw lexer evidence (`OX0001 unexpected character`, all arms including rust):
`#` 3644 (granite only), `;` 3490 (qwen 1618 / cg 1358 / gr 514), `—` 728
(granite only), `[`/`]` 450+450, `` ` `` 176 (granite), `'` 143, `&` 141,
`|` 140.

### Reached-for syntax, programs affected (of 400) and total occurrences

| habit | qwen | codegemma | granite |
|---|---|---|---|
| semicolon at end of line | **291p / 1606occ** | **279p / 1348occ** | 72p / 495occ |
| `&` borrow sigil | **122p / 299occ** | **110p / 238occ** | 41p / 193occ |
| `.iter()/.map()/.collect()` chains | **55p / 108occ** | 42p / 78occ | 11p / 21occ |
| `a..b` / `a..=b` ranges | 37p / 53occ | **74p / 97occ** | 14p / 15occ¹ |
| `name!(...)` macro call | 19p / 28occ | **79p / 134occ** | 13p / 27occ |
| `v[i]` bracket indexing | 37p / 116occ | 39p / 70occ | 9p / 17occ |
| `\|x\|` closures | 14p / 15occ | 15p / 19occ | 7p / 11occ |
| `::` path separator | 30p / 62occ | 29p / 92occ | 13p / 44occ |
| `for x in &v` | 40p / 48occ | 24p / 27occ | 16p / 22occ |
| `String` / `&str` types | 12p / 21occ | 34p / 44occ | 8p / 19occ |
| `if let` / `while let` | 12p / 19occ | 8p / 9occ | 3p / 3occ |
| tuple destructuring `let (a, b) =` | 16p / 16occ | 14p / 14occ | 6p / 8occ |
| `impl` block | 2p / 4occ | 5p / 6occ | 4p / 10occ |
| **language-card builtin-block echo** | 0p / 0occ | 0p / 0occ | **282p / 701occ** |

¹ granite's raw `..` count is 294p/295occ, but 280 of those programs get it
solely from the echoed card line `range(a, b) -> Vec<Int>   # integers a..b-1`.
Excluding lines containing `#`, real range demand is 14p/15occ.

### Top 3 habits per family

- **qwen:** (1) semicolons 291/400 programs; (2) `&` borrows 122/400;
  (3) iterator method chains 55/400.
- **codegemma:** (1) semicolons 279/400; (2) `&` borrows 110/400;
  (3) `println!`/`vec!` macros 79/400 — with `a..b` ranges immediately behind
  at 74/400.
- **granite:** (1) verbatim echo of the card's Builtins block 282/400;
  (2) semicolons 72/400; (3) `&` borrows 41/400. Granite is the *least*
  Rust-syntactic of the three and the most prone to copying the prompt.

Macro names actually used (all arms): qwen `println` 305, `vec` 133,
`format` 6; codegemma `println` 361, `vec` 172, `print` 73, `format` 8,
`assert_eq` 6, `write` 4, `panic` 4; granite `println` 339, `vec` 45,
`print` 8.

### One snippet per habit per family

**qwen — semicolons**
```
    let total = 0;
    total = total + (x * x);
```
`U/g0u-qwen7b-0shot-s7` t01.explicit L2, L4

**qwen — `&` borrows**
```
fn count_positive(v: &Vec<Int>) -> Int {
```
`U/g0u-qwen7b-0shot-s7` t08.explicit L1

**qwen — iterator chains / bracket indexing**
```
    let reversed: Str = text.chars().map(|c| c).collect();
```
`U/g0u-qwen7b-0shot-s7` t13.oxide L4
```
        if sorted[j] > sorted[j + 1] {
            let temp = sorted[j];
            sorted[j] = sorted[j + 1];
```
`U/g0u-qwen7b-0shot-s7` t11.explicit L6–8

**codegemma — semicolons**
```
    let mut sum = 0;
    sum += i * i;
```
`U/g0u-codegemma7b-0shot-s10` t01.rust L2, L4 (identical shape appears in the
oxide/explicit arms)

**codegemma — `&` borrows**
```
    for num in &list {
```
`U/g0u-codegemma7b-0shot-s10` t07.explicit L9

**codegemma — macros / ranges**
```
    for i in 0..=n {
```
`U/g0u-codegemma7b-0shot-s10` t01.explicit L3
```
    let sum = range(0, 10).iter().map(|x| x * x).fold(0, |acc, x| acc + x);
```
`U/g0u-codegemma7b-0shot-s7` t01.oxide L2 — note this mixes the *Black Oxide*
`range(0, 10)` builtin with Rust iterator/closure syntax.

**granite — card echo**
```
print(x)                      # debug-print any value
print_str(s)                  # print a Str without quotes
vec() -> Vec<T>               # empty vector (needs usage context to infer T)
push(v, x) -> Vec<T>          # consumes v, returns it with x appended
```
`U/g0u-granite8b-0shot-s10` t01.oxide L1–4 — the Builtins block of
`LANGUAGE_CARD.md` reproduced verbatim as program text. The `explicit` arm
echoes the explicit card's variant, em-dashes and all:
`print(x)   # reads x — debug-print` (`U/g0u-granite8b-0shot-s10` t01.explicit).

**granite — semicolons / `&`**
```
    for &value in &values {
```
`U/g0u-granite8b-0shot-s10` t08.rust L6
```
fn reverse(s: &str) -> String {
```
`U/g0u-granite8b-0shot-s10` t12.oxide L1

### Parser-level demand (unconstrained OX01xx)

- `OX0100 expected expression, found EQ` 85 (qwen 23, cg 54, gr 8) — assignment
  in an expression position, e.g. compound assignment or `x[i] = v`.
- `OX0100 expected expression, found STAR` 36 — dereference `*x`.
- `OX0101 expected end of statement, found BANG` 223 (cg 149) — macro `!`.
- `OX0101 expected field name, found DOT` 64 / `found INT` 25 — `..` range and
  tuple field access `t.0`.
- `OX0101 expected parameter name, found AMP` 21 (granite 16) — `&self`/`&v`
  parameters.
- `OX0101 expected end of statement, found PATH_SEP` 42 — `Shape::Circle`,
  `String::from`, turbofish `sum::<u32>()`.
- `OX0103 expected type, found LPAREN` 38 — tuple types `(Int, Int)`.
- `OX0104 expected pattern, found LPAREN` 39 — tuple patterns
  `let (a, b) = ...` and tuple match arms.
- `OX0102 expected item at module level, found IDENT` 514 (granite 320) —
  prose/card text at top level, granite's echo again.

---

## Cross-cutting observations

**C1 — Constrained decoding fuses keywords into identifiers, and the fused
token is what the diagnostic reports.** Because the grammar has no `if let`,
no `mut` in parameter position, and no field-path assignment, the decoder
emits the *adjacent* tokens as one identifier or swaps `=` for `==`:

- `mut n: Int` → `mutn: Int` → six spurious `unknown identifier 'n'`
  (`S/resolve/OX0200/g0c-codegemma7b-0shot-s1.t02.explicit.txt`).
- `if let Some(m) = max` → `if letSome(m) == max`
  (`C/g0c-qwen7b-0shot-s9` t08.explicit L8). 21 `letSome` instances.
- `s.values = push(s.values, next)` → `s.values == push(...)`
  (`C/g0c-granite8b-0shot-s6` t05.oxide L10).
- `while i < len(v)` inside an expression → `whilei < len(v)`
  (`C/g0c-granite8b-0shot-s6` t07.oxide L7).

Programs showing at least one keyword-fusion or `mut<ident>` parameter:
qwen 17, codegemma 15, granite 16 (of 400 each). The consequence for the
taxonomy is that a nontrivial share of OX0200 `D1-unbound-variable` and OX0300
`Bool vs Unit` is **downstream of a suppressed syntax**, not an independent
naming or typing error.

**C2 — Models re-emit the language card's example program as if it were part of
the answer.** Programs containing ≥2 card markers (`first_big`, `Reading {`,
`too short`, `label: "lab"`, `field access copies` for the oxide card;
`sum_big`, `extend`, `drop nums`, `drop bigger` for the explicit card):
**granite 44 / 400, qwen 7 / 400, codegemma 2 / 400.**

```
    let r = Reading { label: "lab", values: push(push(vec(), 3), 42) }
    print(first_big(r.values))            // field access copies: r stays usable
    let r2 = Reading { label: "lab2", ..r }
    match second(r2.values) {
        Some(n) => print(n),
        None => print_str("too short"),
    }
```
`C/g0c-granite8b-0shot-s6` t08.oxide L110–116 — appended after the model's own
`main`, comment included, producing a duplicate `main` plus `unknown identifier
'first_big'`/`'second'`/`'Reading'`.

The explicit-card counterpart:
```
fn sum_big(v: &Vec<Int>, limit: Int) -> Int { ... }
fn main() {
    let nums = push(push(vec(), 5), 40)
    let bigger = extend(clone(&nums), 70)
    print(sum_big(&nums, 10))
    drop nums
```
`C/g0c-granite8b-0shot-s5` t01.explicit L1–14, emitted *before* the real
solution. In the unconstrained condition the same behaviour scales up: granite
echoes the card's **Builtins block** in 282 / 400 programs (§ Unconstrained).

**C3 — Models rebuild the standard library rather than trust the builtin list.**
44 programs (granite 27, qwen 9, codegemma 8) define at least one function
whose name is already a builtin. Redefined builtins in rank order: `print` 42,
`print_str` 33, `vec` 27, `get` 17, `parse_int` 17, `push` 16, `int_to_str` 14,
`len` 12, `clone` 10, `range` 8, `chars` 8, `trunc` 8, `str_len` 7,
`to_float` 5. This is the single largest source of OX0203 diagnostics for qwen
(103 of 111) and granite (109 of 310). The same reflex also produces user
`struct Option`/`struct Some`/`struct None` (`C/g0c-granite8b-0shot-s6`
t08.oxide L81–91) and, in one codegemma program, user `enum` declarations for
`Result`, `Option`, `Vec`, `Str`, `Int`, `Float`, `Bool`, `Char`, `Some`,
`None`, `Ok`, `Err` (`C/g0c-codegemma7b-0shot-s9` t19.oxide).

**C4 — Two tasks drive two thirds of OX0203; the code is not uniformly
distributed.** Task concentration (diagnostic share of each code's total):

- OX0203: **t19 = 285 (39%)**, **t20 = 199 (27%)**, t18 = 59 (8%), t16 = 43,
  t08 = 30. t19 ("rectangle: print area then perimeter") and t20 ("three
  inventory items, combined cost and most expensive") are the two tasks that
  most invite per-type helper functions, and they are also where verbatim
  repetition clusters: 9 of the 16 repetition programs are t19/t20, supplying
  206 of the 484 OX0203 diagnostics from those two tasks.
- OX0306: t13 = 67 (17%), t04 = 47 (12%), t17 = 34 (9%), t11 = 29, t12 = 28 —
  the string/palindrome, prime, bounded-lookup and sort tasks, i.e. the ones
  needing `chars`/`nth`/`sqrt`/`unwrap_or`/`set`.
- OX0303: t09 = 31 (17%), t10 = 31 (17%), t11 = 27 (14%), t17 = 25 (13%),
  t08 = 19 (10%) — every one of these begins "a list contains …", which is
  exactly the `vec(a, b, c)` trigger.
- OX0200: flatter — t10 = 113 (11%), t11 = 93, t14 = 79, t08 = 78, t15 = 64.
- OX0300: flattest — t10 = 26 (11%), t04 = 25, t05 = 21, t13 = 20, t11 = 17.

**C5 — Generation degeneracy and truncation are non-trivial confounds.**
Verbatim whole-program repetition: granite 21 / 400, codegemma 16 / 400,
qwen 1 / 400. Programs whose text does not end in `}` or `)` (truncated tail):
granite 47, codegemma 41, qwen 27. One granite program is pure token
degeneracy:
```
fn next_seq(a: Int, b: Int) -> Int {
    CSRF
    CSRF
}
```
`S/resolve/OX0200/g0c-granite8b-0shot-s1.t05.explicit.txt` (4 OX0200). Another
emits a 200+-element `push("a").push("b")…` chain of every ASCII character
(`C/g0c-granite8b-0shot-s5` t12.oxide L3). These programs contribute
diagnostics disproportionately (the 16 repetition programs alone contribute
269 of 740 OX0203 diagnostics, 36%).

**C6 — Higher-order functions are demanded by name, not by lambda, under
constraint.** Lines passing a named top-level function into a combinator:
qwen 53, granite 47, codegemma 30.
```
    let pos = len(filter(v, is_positive))
    ...
    let largest = reduce(v, max)
```
`C/g0c-granite8b-0shot-s6` t08.oxide L7–9
```
    let result = multiples.iter().filter(is_multiple_of_three).map(sum_of_multiples).fold(0, sum)
```
`C/g0c-codegemma7b-0shot-s2` t07.explicit L13

Three granite programs go further and invent a function *type*:
```
fn reduce(f: Func<Int, Int, Int>, base: Int, v: Vec<Int>) -> Int {
```
`S/resolve/OX0203/g0c-granite8b-0shot-s1.t07.oxide.txt` L7 (also
`f: Fun<Int, Int>` in `C/g0c-granite8b-0shot-s5` t04.oxide L28 and
`f: Fn<T>` in `C/g0c-granite8b-0shot-s8` t13.oxide L160). In the unconstrained
condition the same demand appears as `|x| ...` closures (qwen 14p, cg 15p,
gr 7p of 400) — so constraint converts closure demand into named-function-
reference demand, not into loops.

**C7 — `&` leaks into the `oxide` arm, which was never taught it.** The oxide
card contains no borrow sigil; the explicit card does. Programs in the *oxide*
arm containing `&`: codegemma 11 / 200, granite 11 / 200, qwen 3 / 200.
Correspondingly, OX0306 is 2.2× more frequent in the explicit arm (267 vs 120)
— receiver-method syntax co-occurs with the borrow-heavy dialect.

**C8 — The card's own "no user methods" rule is the one prohibition models
reliably obey.** Only 2 of 387 OX0306 diagnostics (0.5%) are user-struct
method calls, and only 4 programs corpus-wide call `x.f()` where `fn f` is
defined top-level. The demand that the rule suppresses does not disappear; it
resurfaces as OX0203 type-based overloading (15 programs, `perimeter`×47,
`area`×38, `cost`×24) and as the free-function+UFCS hybrid in
`S/types/OX0303/g0c-granite8b-0shot-s2.t18.explicit.txt`.

**C9 — `get`'s `Option` return is the most-worked-around builtin signature.**
Across codes: 63 `.unwrap()` and 28 `.unwrap_or()` receiver-method calls, 58 of
63 `unwrap` receivers being `get(...)` chains (OX0306); 31 OX0300 diagnostics
from using a `get` result as a bare element; 11 OX0300 from `?` in a
Unit-returning function; 37 qwen `.set(` calls wanting the write side of the
same operation. Two granite/codegemma cases bounds-check first and still expect
a bare element (`C/g0c-granite8b-0shot-s6` t17.explicit L2–3).

**C10 — `vec()` is read as a literal constructor, not as an empty-vector
factory.** 148 OX0303 diagnostics (79% of that code) are `vec(a, b, …)` with
arity 2–10 matching the task's list length exactly, plus 53 OX0200 diagnostics
calling the *type* `Vec()`/`Str()`/`String()` in the same role, plus 27 OX0203
diagnostics from models defining their own `fn vec()`. Together that is ~230
diagnostics traceable to one signature.
