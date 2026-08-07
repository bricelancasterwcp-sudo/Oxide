"""Blind TDD tests for Phase 5a.1 (v0.2.1) Rust codegen — SPEC.md Part VII, section 38 file 2.

W1-W5/W7 rustc + pinned stdout; W3 `.clone()`; `?` verbatim; before-jump drop
text before `break;`; section 37 prelude additions verbatim; combined
break + continue + `?` + functional-update program. Written blind against
SPEC.md sections 34-38 (Parts I-V as amended); implementation lands concurrently.
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
# Golden sources (section 36 W-goldens, given fn bodies + a driving main)
# ---------------------------------------------------------------------------

W1_SRC = """\
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
    print(first_big(push(push(push(vec(), 3), 42), 99)))
}
"""

W2_SRC = """\
fn second(v: Vec<Int>) -> Option<Int> {
    let x = get(v, 1)?
    Some(x + 1)
}

fn main() {
    match second(push(push(vec(), 5), 6)) {
        Some(n) => print(n),
        None => print(-1),
    }
}
"""

W3_SRC = """\
struct Bag {
    items: Vec<Int>,
}

fn main() {
    let b = Bag { items: push(vec(), 4) }
    let c = b.items
    print(len(c))
    print(len(b.items))
}
"""

W4_SRC = """\
struct Point {
    x: Int,
    y: Int,
}

fn main() {
    let p = Point { x: 1, y: 2 }
    let q = Point { x: 5, ..p }
    let Point { x, y } = q
    print(x + y)
}
"""

W5_SRC = """\
fn sum_odds(n: Int) -> Int {
    let s = 0
    for i in range(0, n) {
        if i % 2 == 0 {
            continue
        }
        s = s + i
    }
    s
}

fn main() {
    print(sum_odds(6))
}
"""

W7_SRC = """\
fn main() {
    print(trunc(to_float(7) / 2.0))
}
"""

# Loop-local vec still owned at a conditional break -> before-jump drop
# (section 36) emitted immediately before `break;` (section 37).
BREAK_DROP_SRC = """\
fn main() {
    while true {
        let t = push(vec(), 7)
        if len(t) > 0 {
            break
        }
        print(len(t))
    }
}
"""

# Combined break + continue + `?` + functional update program (section 38).
# scan: 3 added, -1 skipped by continue, 4 added, 200 breaks -> 7.
# bump: Pair { a: 7 + 10, ..p } -> Pair { a: 17, b: 5 }; 17 + 5 = 22.
# first: get(v, 0)? propagates -> Some(22) -> prints 22.
COMBINED_SRC = """\
struct Pair {
    a: Int,
    b: Int,
}

fn scan(v: Vec<Int>) -> Int {
    let total = 0
    for x in v {
        if x < 0 {
            continue
        }
        if x > 99 {
            break
        }
        total = total + x
    }
    total
}

fn bump(p: Pair) -> Pair {
    Pair { a: p.a + 10, ..p }
}

fn first(v: Vec<Int>) -> Option<Int> {
    let x = get(v, 0)?
    Some(x)
}

fn main() {
    let v = push(push(push(push(vec(), 3), -1), 4), 200)
    let s = scan(v)
    let p = bump(Pair { a: s, b: 5 })
    let Pair { a, b } = p
    match first(push(vec(), a + b)) {
        Some(n) => print(n),
        None => print(-1),
    }
}
"""

ALL_CLEAN_SOURCES = [
    pytest.param(W1_SRC, id="w1_break"),
    pytest.param(W2_SRC, id="w2_try"),
    pytest.param(W3_SRC, id="w3_field_clone"),
    pytest.param(W4_SRC, id="w4_functional_update"),
    pytest.param(W5_SRC, id="w5_continue"),
    pytest.param(W7_SRC, id="w7_to_float_trunc"),
    pytest.param(BREAK_DROP_SRC, id="break_drop"),
    pytest.param(COMBINED_SRC, id="combined"),
]

# Section 23 prelude + section 29 additions (kept verbatim) + section 37
# additions, in order, each separated by one blank line.
PRELUDE_V02 = """\
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
}"""

PRELUDE_V021_ADDITIONS = """\
fn to_float(x: i64) -> f64 {
    x as f64
}

fn trunc(x: f64) -> i64 {
    x as i64
}"""

FULL_PRELUDE = PRELUDE_V02 + "\n\n" + PRELUDE_V021_ADDITIONS


# ---------------------------------------------------------------------------
# Plan item 1 — W1-W5, W7 compile via rustc and produce the pinned stdout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ALL_CLEAN_SOURCES)
def test_v021_sources_transpile_clean(source):
    assert diag_codes(analyze(source)) == []
    transpile_ok(source)


def test_w1_emit_rust_matches_transpile():
    res = analyze(W1_SRC)
    assert diag_codes(res) == []
    assert emit_rust(res) == transpile_ok(W1_SRC)


@needs_rustc
def test_w1_break_runtime_stdout(tmp_path):
    assert compile_and_run(W1_SRC, tmp_path) == "42\n"


@needs_rustc
def test_w2_try_runtime_stdout(tmp_path):
    assert compile_and_run(W2_SRC, tmp_path) == "7\n"


@needs_rustc
def test_w3_field_clone_runtime_stdout(tmp_path):
    assert compile_and_run(W3_SRC, tmp_path) == "1\n1\n"


@needs_rustc
def test_w4_functional_update_runtime_stdout(tmp_path):
    assert compile_and_run(W4_SRC, tmp_path) == "7\n"


@needs_rustc
def test_w5_continue_runtime_stdout(tmp_path):
    assert compile_and_run(W5_SRC, tmp_path) == "9\n"


@needs_rustc
def test_w7_to_float_trunc_runtime_stdout(tmp_path):
    assert compile_and_run(W7_SRC, tmp_path) == "3\n"


# ---------------------------------------------------------------------------
# Plan item 2 — W3 emits `.clone()` for the field access (section 37)
# ---------------------------------------------------------------------------


def test_w3_emits_clone_for_field_access():
    rust = transpile_ok(W3_SRC)
    # Both non-copy field-access sites clone; copy destructured x/y in W4 don't.
    assert rust.count("b.items.clone()") == 2
    # The let-bound clone line is fully pinned by sections 22 + 37.
    assert "let c: Vec<i64> = b.items.clone();" in rust


def test_w4_copy_field_semantics_no_clone_in_combined_bump():
    rust = transpile_ok(COMBINED_SRC)
    # p.a is a copy (Int) field: emitted unchanged, never cloned.
    assert "p.a + 10" in rust
    assert "p.a.clone()" not in rust


# ---------------------------------------------------------------------------
# Plan item 3 — `?` emits verbatim (section 37)
# ---------------------------------------------------------------------------


def test_try_operator_emits_verbatim():
    rust = transpile_ok(W2_SRC)
    # v is a ref-bound read param inside `second`, so the arg is bare.
    assert "get(v, 1)?" in rust
    assert "let x: i64 = get(v, 1)?;" in rust


def test_try_operator_verbatim_in_combined():
    rust = transpile_ok(COMBINED_SRC)
    assert "get(v, 0)?" in rust


# ---------------------------------------------------------------------------
# Plan item 4 — before-jump drop text appears before `break;`
# ---------------------------------------------------------------------------


def test_before_jump_drop_in_drop_list():
    res = analyze(BREAK_DROP_SRC)
    assert diag_codes(res) == []
    assert ("main", "t", "before-jump") in drop_list(res)


def test_before_jump_drop_emitted_before_break():
    rust = transpile_ok(BREAK_DROP_SRC)
    # drop of the loop-local vec immediately precedes the jump, inside the
    # if body (fn=4, while=8, if=12 spaces of indent).
    assert "            drop(t);\n            break;" in rust


@needs_rustc
def test_before_jump_program_compiles_and_runs(tmp_path):
    # Breaks on the first iteration, printing nothing.
    assert compile_and_run(BREAK_DROP_SRC, tmp_path) == ""


# ---------------------------------------------------------------------------
# Plan item 5 — prelude additions verbatim (section 37)
# ---------------------------------------------------------------------------


def test_prelude_additions_verbatim():
    rust = transpile_ok("")
    assert PRELUDE_V021_ADDITIONS in rust


def test_prelude_additions_appended_in_order():
    # to_float then trunc, appended after the v0.2 prelude, one blank line
    # between each function (section 37: "same style, in order").
    rust = transpile_ok("")
    assert FULL_PRELUDE in rust
    assert rust.index("fn to_float") < rust.index("fn trunc")


def test_prelude_additions_in_every_output():
    assert PRELUDE_V021_ADDITIONS in transpile_ok(W1_SRC)
    assert PRELUDE_V021_ADDITIONS in transpile_ok(COMBINED_SRC)


# ---------------------------------------------------------------------------
# Plan item 6 — combined break+continue+?+update program compiles and runs
# ---------------------------------------------------------------------------


def test_combined_program_emitted_forms():
    rust = transpile_ok(COMBINED_SRC)
    assert "break;" in rust
    assert "continue;" in rust
    assert "get(v, 0)?" in rust
    # Functional update emits identical Rust syntax (section 37).
    assert "Pair { a: p.a + 10, ..p }" in rust


def test_w4_functional_update_emitted_form():
    rust = transpile_ok(W4_SRC)
    assert "Point { x: 5, ..p }" in rust


@needs_rustc
def test_combined_program_compiles(tmp_path):
    compile_rust(transpile_ok(COMBINED_SRC), tmp_path)


@needs_rustc
def test_combined_program_runtime_stdout(tmp_path):
    # scan -> 7 (continue skips -1, break stops at 200); bump -> {17, 5};
    # first(vec![22]) -> Some(22).
    assert compile_and_run(COMBINED_SRC, tmp_path) == "22\n"


# ---------------------------------------------------------------------------
# Regressions — accepted-implies-compiles on diverging / unreachable paths
# ---------------------------------------------------------------------------

DIVERGING_TAIL_SRC = """\
fn f(c: Bool) {
    while true {
        let w = push(vec(), 1)
        if c {
            break
        }
    }
}

fn main() {
    f(true)
}
"""


@needs_rustc
def test_block_end_drop_after_diverging_tail(tmp_path):
    """Regression: the linear checker fires block-end drops only on normal
    fall-through and gives jump paths their own before-jump drops, but
    codegen emitted block-end drops BEFORE the block's tail (section 22
    placement). With a tail `if` containing `break`, the break path ran
    both drops -> rustc E0382 on an analyze-clean program (upstream root
    cause: section 22 vs section 36 placement conflict; codegen now
    emits a potentially-diverging tail first, putting block-end drops on
    the fall-through edge only)."""
    rust = transpile_ok(DIVERGING_TAIL_SRC)
    body = rust[rust.index("fn f(") :]
    # before-jump drop inside the if, fall-through drop after it — in order.
    assert (
        "        if c {\n"
        "            drop(w);\n"
        "            break;\n"
        "        }\n"
        "        drop(w);\n" in body
    )
    assert compile_and_run(DIVERGING_TAIL_SRC, tmp_path) == ""


UNREACHABLE_MOVE_SRC = """\
fn f(v: Vec<Int>) {
    while true {
        break
        let w = v
    }
    print(len(v))
}

fn main() {
    f(push(vec(), 1))
}
"""


@needs_rustc
def test_unreachable_stmts_after_jump_not_emitted(tmp_path):
    """Regression: statements after break/continue/return are invisible to
    mode inference (cfg._scan_moves stops at jump nodes, so the dead
    `let w = v` contributed no own-mode evidence and `v` stayed a
    ref-bound read param) and to the linear checker (_DIVERGED), yet
    codegen emitted them verbatim -> rustc E0308 (`expected Vec<i64>,
    found &Vec<i64>`) on an analyze-clean program. Codegen now mirrors
    the analyses' reachability and skips statements after a diverging
    statement."""
    res = analyze(UNREACHABLE_MOVE_SRC)
    assert diag_codes(res) == []
    assert res.modes.modes["f"] == ("read",)  # dead move: no own evidence
    rust = transpile_ok(UNREACHABLE_MOVE_SRC)
    assert "let w" not in rust  # the unreachable move is not emitted
    assert compile_and_run(UNREACHABLE_MOVE_SRC, tmp_path) == "1\n"
