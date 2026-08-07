"""Blind TDD tests for Phase 5a (v0.2) Rust codegen — SPEC.md Part V, section 31 file 2.

Covers all 8 plan items: R5/R6 item-exact emission and R5/R6/R7 runtime stdout,
amended derives, amended prelude, `mut` inference, variant emission forms,
a rustc compile battery, the combined-program runtime, and transpile error
returns for V4/V6.

Written blind against SPEC.md sections 21-25 (Part IV, as amended) and 26-31
(Part V); the implementation lands concurrently.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from src.codegen.rust import emit_rust, transpile
from src.sema.analyze import analyze, diag_codes, drop_list

RUSTC = shutil.which("rustc") or (
    "/home/brice/.cargo/bin/rustc"
    if os.path.exists("/home/brice/.cargo/bin/rustc")
    else None
)

needs_rustc = pytest.mark.skipif(RUSTC is None, reason="rustc not available")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def transpile_ok(source: str) -> str:
    rust, diags = transpile(source)
    codes = [d.code for d in diags]
    assert codes == [], f"unexpected diagnostics: {codes}"
    assert rust is not None
    return rust


def assert_item_exact(rust: str, item: str) -> None:
    """Item-exact per section 25: substring bounded by blank lines (or EOF)."""
    assert (
        f"\n\n{item}\n\n" in rust or rust.endswith(f"\n\n{item}\n")
    ), f"item not found bounded by blank lines in output:\n{item}\n--- output ---\n{rust}"


def compile_rust(rust_text: str, tmp_path):
    src = tmp_path / "main.rs"
    src.write_text(rust_text)
    out = tmp_path / "oxide_bin"
    proc = subprocess.run(
        [RUSTC, "--edition", "2021", str(src), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"rustc failed:\n{proc.stderr}"
    return out


def compile_and_run(source: str, tmp_path) -> str:
    binary = compile_rust(transpile_ok(source), tmp_path)
    proc = subprocess.run([str(binary)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# ---------------------------------------------------------------------------
# Sources and golden texts
# ---------------------------------------------------------------------------

R5_SRC = """\
enum Shape {
    Circle(Float),
    Rect(Float, Float),
    Empty,
}

fn describe(s: Shape) -> Float {
    match s {
        Circle(r) => r * r,
        Rect(w, h) => w * h,
        Empty => 0.0,
    }
}

fn main() {
    print(describe(Rect(3.0, 4.0)))
}
"""

R5_ITEMS = """\
#[derive(Debug, Clone)]
enum Shape {
    Circle(f64),
    Rect(f64, f64),
    Empty,
}

fn describe(s: Shape) -> f64 {
    match s {
        Shape::Circle(r) => r * r,
        Shape::Rect(w, h) => w * h,
        Shape::Empty => 0.0,
    }
}"""

R6_SRC = """\
fn sum_squares(n: Int) -> Int {
    let total = 0
    for i in range(0, n) {
        total = total + i * i
    }
    total
}

fn main() {
    print(sum_squares(5))
}
"""

R6_ITEM = """\
fn sum_squares(n: i64) -> i64 {
    let mut total: i64 = 0;
    for i in range(0, n).iter().cloned() {
        total = total + i * i;
    }
    total
}"""

R7_SRC = """\
fn main() {
    let v = push(push(vec(), 10), 20)
    match get(v, 1) {
        Some(x) => print(x),
        None => print(-1),
    }
}
"""

R4_SRC = """\
struct Point {
    x: Int,
    y: Int,
}

fn area(p: Point) -> Int {
    let Point { x, y } = p
    x * y
}

fn main() {
    let p = Point { x: 6, y: 7 }
    print(area(p))
}
"""

R4_STRUCT_ITEM = """\
#[derive(Debug, Clone)]
struct Point {
    x: i64,
    y: i64,
}"""

EQ_STRUCT_SRC = """\
struct P {
    x: Int,
}

fn same(a: P, b: P) -> Bool {
    a == b
}
"""

MUT_LET_SRC = """\
fn f() -> Int {
    let a = 1
    let b = 2
    a = a + b
    a
}
"""

MUT_PARAM_SRC = """\
fn bump(n: Int) -> Int {
    n = n + 1
    n
}
"""

WILDCARD_SRC = """\
enum Color {
    Red,
    Green,
    Blue,
}

fn name_it(c: Color) -> Int {
    match c {
        Red => 1,
        _ => 0,
    }
}
"""

V3_SRC = "fn f(v: Vec<Int>) { for x in v { print(x) } }\n"

V5_SRC = "fn f(o: Option<Int>) -> Int { match o { Some(x) => x, None => 0 } }\n"

V7_SRC = """\
fn f() {
    let v = push(vec(), 1)
    let w = v
    v = push(vec(), 2)
    print(len(v))
    print(len(w))
}
"""

COMBINED_SRC = """\
enum Op {
    Add(Int),
    Skip,
}

fn apply(total: Int, o: Op) -> Int {
    match o {
        Add(k) => total + k,
        Skip => total,
    }
}

fn main() {
    let total = 0
    for i in range(1, 4) {
        total = apply(total, Add(i))
    }
    print(total)
}
"""

STRINGS_SRC = """\
fn main() {
    let a = concat("ab", "cd")
    print_str(a)
    let cs = chars("hey")
    print(len(cs))
}
"""

MATCH_IN_LET_SRC = """\
fn main() {
    let v = push(vec(), 5)
    let n = match get(v, 0) {
        Some(x) => x,
        None => 0,
    }
    print(n)
    print(len(v))
}
"""

V4_SRC = """\
enum Shape {
    Circle(Float),
    Rect(Float, Float),
    Empty,
}

fn describe(s: Shape) -> Float {
    match s {
        Circle(r) => r * r,
        Rect(w, h) => w * h,
    }
}
"""

V6_SRC = """\
fn f(o: Option<Int>, v: Vec<Int>) {
    match o {
        Some(x) => { let w = push(v, x) },
        None => { print(0) },
    }
    print(len(v))
}
"""

# Section 23 prelude (kept verbatim) + section 29 additions, in order, each
# separated by one blank line.
PRELUDE = """\
#![allow(dead_code)]

fn print<T: std::fmt::Debug>(x: &T) {
    println!("{:?}", x);
}

fn len<T>(v: &Vec<T>) -> i64 {
    v.len() as i64
}

fn push<T>(mut v: Vec<T>, x: T) -> Vec<T> {
    v.push(x);
    v
}

fn vec<T>() -> Vec<T> {
    Vec::new()
}

fn clone<T: Clone>(x: &T) -> T {
    x.clone()
}

fn get<T: Clone>(v: &Vec<T>, i: i64) -> Option<T> {
    if i < 0 {
        return None;
    }
    v.get(i as usize).cloned()
}

fn range(a: i64, b: i64) -> Vec<i64> {
    (a..b).collect()
}

fn print_str(s: &String) {
    println!("{}", s);
}

fn str_len(s: &String) -> i64 {
    s.chars().count() as i64
}

fn concat(a: String, b: String) -> String {
    a + &b
}

fn chars(s: &String) -> Vec<String> {
    s.chars().map(|c| c.to_string()).collect()
}

fn int_to_str(x: i64) -> String {
    x.to_string()
}

fn parse_int(s: &String) -> Option<i64> {
    s.trim().parse::<i64>().ok()
}

fn to_float(x: i64) -> f64 {
    x as f64
}

fn trunc(x: f64) -> i64 {
    x as i64
}"""


# ---------------------------------------------------------------------------
# Item 1 — R5/R6 item-exact; R5/R6/R7 compile and run with pinned stdout
# ---------------------------------------------------------------------------


def test_r5_enum_and_describe_item_exact():
    rust = transpile_ok(R5_SRC)
    assert_item_exact(rust, R5_ITEMS)


def test_r6_sum_squares_item_exact():
    rust = transpile_ok(R6_SRC)
    assert_item_exact(rust, R6_ITEM)


def test_r6_emit_rust_matches_transpile():
    res = analyze(R6_SRC)
    assert diag_codes(res) == []
    assert emit_rust(res) == transpile_ok(R6_SRC)


def test_r7_analyze_clean_with_one_after_stmt_drop():
    res = analyze(R7_SRC)
    assert diag_codes(res) == []
    assert drop_list(res) == [("main", "v", "after-stmt")]


@needs_rustc
def test_r5_runtime_stdout(tmp_path):
    assert compile_and_run(R5_SRC, tmp_path) == "12.0\n"


@needs_rustc
def test_r6_runtime_stdout(tmp_path):
    assert compile_and_run(R6_SRC, tmp_path) == "30\n"


@needs_rustc
def test_r7_runtime_stdout(tmp_path):
    assert compile_and_run(R7_SRC, tmp_path) == "20\n"


# ---------------------------------------------------------------------------
# Item 2 — amended derives
# ---------------------------------------------------------------------------


def test_r4_struct_emits_debug_clone_derive():
    rust = transpile_ok(R4_SRC)
    assert_item_exact(rust, R4_STRUCT_ITEM)
    assert "#[derive(Debug)]\n" not in rust


def test_eq_used_struct_emits_partialeq_derive():
    rust = transpile_ok(EQ_STRUCT_SRC)
    assert "#[derive(Debug, Clone, PartialEq)]\nstruct P {" in rust


# ---------------------------------------------------------------------------
# Item 3 — amended prelude verbatim
# ---------------------------------------------------------------------------


def test_amended_prelude_present_verbatim():
    rust = transpile_ok("")
    assert PRELUDE in rust


def test_prelude_spot_checks():
    rust = transpile_ok("")
    assert "fn get<" in rust
    assert "fn parse_int" in rust


def test_prelude_in_every_output():
    assert PRELUDE in transpile_ok(R5_SRC)
    assert PRELUDE in transpile_ok(R6_SRC)


# ---------------------------------------------------------------------------
# Item 4 — mut inference
# ---------------------------------------------------------------------------


def test_assigned_var_emits_let_mut_and_unassigned_plain_let():
    rust = transpile_ok(MUT_LET_SRC)
    assert "let mut a: i64 = 1;" in rust
    assert "let b: i64 = 2;" in rust
    assert "let mut b" not in rust


def test_assigned_param_emits_mut():
    rust = transpile_ok(MUT_PARAM_SRC)
    assert "mut n: i64" in rust
    assert "fn bump(mut n: i64) -> i64 {" in rust


def test_r6_mut_forms():
    rust = transpile_ok(R6_SRC)
    assert "let mut total: i64 = 0;" in rust
    assert "n: i64" in rust
    assert "mut n" not in rust  # n is never assigned in R6


# ---------------------------------------------------------------------------
# Item 5 — variant emission forms
# ---------------------------------------------------------------------------


def test_wildcard_arm_and_qualified_user_variants():
    rust = transpile_ok(WILDCARD_SRC)
    assert "Color::Red => 1," in rust
    assert "_ => 0," in rust


def test_option_variants_emit_bare():
    rust = transpile_ok(R7_SRC)
    assert "Some(x) =>" in rust
    assert "None =>" in rust
    assert "Option::" not in rust
    assert "::Some" not in rust
    assert "::None" not in rust


def test_user_variant_constructor_call_emits_qualified():
    rust = transpile_ok(R5_SRC)
    assert "Shape::Rect(3.0, 4.0)" in rust


# ---------------------------------------------------------------------------
# Item 6 — rustc battery
# ---------------------------------------------------------------------------


@needs_rustc
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(V3_SRC, id="v3_for_over_read_param"),
        pytest.param(V5_SRC, id="v5_option_match"),
        pytest.param(V7_SRC, id="v7_assign_reinit"),
        pytest.param(COMBINED_SRC, id="combined_enum_match_for_assign"),
        pytest.param(STRINGS_SRC, id="chars_concat_print_str"),
        pytest.param(MATCH_IN_LET_SRC, id="match_as_value_in_let"),
    ],
)
def test_rustc_battery_compiles(source, tmp_path):
    rust = transpile_ok(source)
    compile_rust(rust, tmp_path)


# ---------------------------------------------------------------------------
# Item 7 — combined program runtime
# ---------------------------------------------------------------------------


@needs_rustc
def test_combined_program_runtime_stdout(tmp_path):
    # for i in 1..4 accumulates 1 + 2 + 3 via enum-dispatched apply().
    assert compile_and_run(COMBINED_SRC, tmp_path) == "6\n"


# ---------------------------------------------------------------------------
# Item 8 — transpile returns (None, codes) for V4/V6
# ---------------------------------------------------------------------------


def test_transpile_v4_non_exhaustive_match_returns_none():
    rust, diags = transpile(V4_SRC)
    assert rust is None
    codes = [d.code for d in diags]
    assert codes == ["OX0307"]
    assert codes == diag_codes(analyze(V4_SRC))


def test_transpile_v6_arm_move_then_use_returns_none():
    rust, diags = transpile(V6_SRC)
    assert rust is None
    codes = [d.code for d in diags]
    assert codes == ["OX0400"]
    assert codes == diag_codes(analyze(V6_SRC))
