"""Blind TDD tests for Phase 4 Rust codegen (SPEC.md Part IV, sections 21-25).

Golden texts are transcribed byte-for-byte from SPEC.md sections 23 (prelude)
and 24 (R1-R4). The rustc invocation is exactly the one pinned by section 25.
Per the section 25 test-author constraints, the only project imports are
``src.codegen.rust`` and ``src.sema.analyze``.
"""

import shutil
import subprocess

import pytest

from eval.rustc_adapter import find_rustc
from src.codegen.rust import emit_rust, transpile
from src.sema.analyze import analyze

# rustc discovery (section 25: PATH first, then the pinned absolute path),
# resolved through find_rustc() -- the project's single source of truth for
# locating the compiler -- instead of a hardcoded machine-specific path.
RUSTC: str | None = shutil.which(find_rustc())
requires_rustc = pytest.mark.skipif(RUSTC is None, reason="rustc not available")

# ---- Oxide sources (SPEC sections 19, 20, 24, 25) ----

S1_SOURCE = "fn main() { let v = vec()\n let v2 = push(v, 1)\n print(len(v2)) }"
S2_SOURCE = "fn main() { let v = vec()\n let w = push(v, 1)\n print(len(v)) }"
S3_SOURCE = (
    "fn f(v: Vec<Int>) -> Vec<Int> { let a = push(v, 1)\n let b = push(v, 2)\n a }"
)
S4_SOURCE = "fn g(c: Bool, v: Vec<Int>) { if c { let w = push(v, 1) } }"
S5_SOURCE = "fn h(v: Vec<Int>) { while true { let w = push(v, 1) } }"
S7_SOURCE = (
    "struct Point { x: Int, y: Int }\n"
    "fn area(p: Point) -> Int { let Point { x, y } = p\n x * y }"
)
S8_SOURCE = (
    "fn wrap(v: Vec<Int>) -> Vec<Int> { push(v, 1) }\n"
    "fn caller(v: Vec<Int>) { let w = wrap(v)\n print(len(w)) }"
)
S9_SOURCE = "fn bad() { let x = 1 + true }"
S10_SOURCE = "fn f() { print(g)\n print(len) }"
S12_SOURCE = "fn t(v: Vec<Int>) { push(v, 1)\n print(0) }"
R3_SOURCE = (
    "fn m(c: Bool, v: Vec<Int>) -> Int "
    "{ let w = push(v, 1)\n if c { return 0 }\n len(w) }"
)
R4_SOURCE = (
    "struct Point { x: Int, y: Int }\n"
    "fn area(p: Point) -> Int { let Point { x, y } = p\n x * y }\n"
    "fn main() { let p = Point { x: 6, y: 7 }\n print(area(p)) }\n"
)
KEYWORD_SOURCE = "fn main() { let impl = 1\n print(impl) }"
SHADOW_SOURCE = (
    "fn main() { let v = push(vec(), 1)\n let v = push(v, 2)\n print(len(v)) }"
)
REF_FORM_SOURCE = (
    "fn peek(v: Vec<Int>) { print(len(v)) }\n"
    "fn main() { let v = push(vec(), 1)\n peek(v) }"
)
GARBAGE_SOURCES: list[str] = [
    '"\\u{',
    "/*/*/*",
    "\x00\xff@#$",
    "0x 0b2 9e",
    "fn",
    "fn f(",
    "{",
    "}}}",
    "fn f() -> {",
]

# ---- Golden Rust texts (SPEC sections 23-24, byte-exact) ----

PRELUDE = """#![allow(dead_code)]

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

R1_MAIN = """fn main() {
    let v: Vec<i64> = vec();
    let v2: Vec<i64> = push(v, 1);
    print(&len(&v2));
    drop(v2);
}"""

R2_ITEM = """fn g(c: bool, v: Vec<i64>) {
    if c {
        let w: Vec<i64> = push(v, 1);
        drop(w);
    } else {
        drop(v);
    }
}"""

R3_ITEM = """fn m(c: bool, v: Vec<i64>) -> i64 {
    let w: Vec<i64> = push(v, 1);
    if c {
        drop(w);
        return 0;
    }
    let __oxide_ret: i64 = len(&w);
    drop(w);
    __oxide_ret
}"""

R4_ITEMS = """#[derive(Debug, Clone)]
struct Point {
    x: i64,
    y: i64,
}

fn area(p: Point) -> i64 {
    let Point { x, y } = p;
    x * y
}"""

# ---- Helpers ----


def _transpile_ok(source: str) -> str:
    rust, diags = transpile(source)
    assert diags == []
    assert rust is not None
    return rust


def _assert_item(rust: str, item: str) -> None:
    """Item-exact: the item appears bounded by blank lines (or ends the file)."""
    assert "\n\n" + item + "\n\n" in rust or rust.endswith("\n\n" + item + "\n")


def _write(tmp_dir: str, name: str, text: str) -> str:
    path = tmp_dir + "/" + name
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _compile(rust: str, tmp_dir: str) -> str:
    rs_file = _write(tmp_dir, "prog.rs", rust)
    exe = tmp_dir + "/prog_bin"
    proc = subprocess.run(
        [RUSTC, "--edition", "2021", rs_file, "-o", exe],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return exe


def _run(exe: str) -> str:
    proc = subprocess.run([exe], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# ---- 1. Golden emissions R1-R4 (byte-exact per sections 23-24) ----


def test_r1_output_after_header_line_is_prelude_then_main_byte_exact() -> None:
    # Act
    rust = _transpile_ok(S1_SOURCE)
    # Assert: header line, blank line, prelude, one blank line, main item,
    # single trailing newline — everything after the header line is pinned.
    _header, rest = rust.split("\n", 1)
    assert rest == "\n" + PRELUDE + "\n\n" + R1_MAIN + "\n"


def test_r2_branch_end_drop_synthesizes_else_arm() -> None:
    rust = _transpile_ok(S4_SOURCE)
    _assert_item(rust, R2_ITEM)


def test_r3_emits_before_return_drop_and_oxide_ret_tail() -> None:
    rust = _transpile_ok(R3_SOURCE)
    _assert_item(rust, R3_ITEM)


def test_r4_emits_derive_debug_struct_and_destructuring_area() -> None:
    rust = _transpile_ok(R4_SOURCE)
    _assert_item(rust, R4_ITEMS)


# ---- 2. Oracle battery: clean transpile + clean rustc compile ----

ORACLE_PARAMS = [
    pytest.param(S1_SOURCE, id="S1"),
    pytest.param(S4_SOURCE, id="S4"),
    pytest.param(S7_SOURCE, id="S7"),
    pytest.param(S8_SOURCE, id="S8"),
    pytest.param(S12_SOURCE, id="S12"),
    pytest.param(R3_SOURCE, id="R3"),
    pytest.param(R4_SOURCE, id="R4"),
]


@requires_rustc
@pytest.mark.parametrize("source", ORACLE_PARAMS)
def test_oracle_sources_transpile_clean_and_compile(
    source: str, tmp_path: object
) -> None:
    # Act
    rust = _transpile_ok(source)
    # Assert: rustc accepts the emission (warnings tolerated)
    _compile(rust, str(tmp_path))


# ---- 3. Runtime behavior of compiled goldens ----


@requires_rustc
def test_r1_binary_prints_one(tmp_path: object) -> None:
    # Arrange
    rust = _transpile_ok(S1_SOURCE)
    # Act
    exe = _compile(rust, str(tmp_path))
    # Assert
    assert _run(exe) == "1\n"


@requires_rustc
def test_r4_binary_prints_forty_two(tmp_path: object) -> None:
    # Arrange
    rust = _transpile_ok(R4_SOURCE)
    # Act
    exe = _compile(rust, str(tmp_path))
    # Assert
    assert _run(exe) == "42\n"


# ---- 4. Part-III error goldens: (None, diags) with analyze's codes ----

ERROR_GOLDENS = [
    pytest.param(S2_SOURCE, ["OX0400"], id="S2"),
    pytest.param(S3_SOURCE, ["OX0401"], id="S3"),
    pytest.param(S5_SOURCE, ["OX0403"], id="S5"),
    pytest.param(S9_SOURCE, ["OX0300"], id="S9"),
    pytest.param(S10_SOURCE, ["OX0200", "OX0201"], id="S10"),
]


@pytest.mark.parametrize(("source", "expected_codes"), ERROR_GOLDENS)
def test_error_goldens_transpile_to_none_with_analyze_codes(
    source: str, expected_codes: list[str]
) -> None:
    # Act
    rust, diags = transpile(source)
    analyzed = analyze(source)
    # Assert
    assert rust is None
    assert [d.code for d in diags] == expected_codes
    assert [d.code for d in diags] == [d.code for d in analyzed.diagnostics]


# ---- 5. Empty source synthesizes fn main() {} ----


def test_empty_source_appends_synthesized_fn_main() -> None:
    rust = _transpile_ok("")
    # Appended last, file ends with one newline.
    assert rust.endswith("\n\nfn main() {}\n")


# ---- 6. Rust-keyword identifiers escape as raw identifiers ----


def test_rust_keyword_binding_emits_raw_identifier() -> None:
    rust = _transpile_ok(KEYWORD_SOURCE)
    assert "let r#impl: i64 = 1;" in rust
    # Drop-free Unit-typed block tail: section 22 maps tail-to-tail (bare).
    assert "\n    print(&r#impl)\n}" in rust


# ---- 7. Shadow renaming: name, name__2; final drop targets the rename ----


def test_shadowed_binding_renames_and_final_drop_targets_rename() -> None:
    rust = _transpile_ok(SHADOW_SOURCE)
    assert "let v: Vec<i64> = push(vec(), 1);" in rust
    assert "let v__2: Vec<i64> = push(v, 2);" in rust
    assert "drop(v__2);" in rust
    assert "drop(v);" not in rust


# ---- 8. Ref-form at read-mode call sites; ref-bound params forward bare ----


def test_read_mode_param_takes_ref_at_call_site_and_forwards_bare() -> None:
    rust = _transpile_ok(REF_FORM_SOURCE)
    # Callee binds &Vec, call site passes &v, forwarding stays bare.
    assert "fn peek(v: &Vec<i64>) {" in rust
    assert "peek(&v);" in rust
    # Drop-free Unit-typed block tail: section 22 maps tail-to-tail (bare).
    assert "\n    print(&len(v))\n}" in rust


# ---- 9. Discarded non-copy temporary wraps the statement in drop(...) ----


def test_discarded_noncopy_temp_is_wrapped_in_drop_call() -> None:
    rust = _transpile_ok(S12_SOURCE)
    assert "drop(push(" in rust
    assert "drop(push(v, 1));" in rust


# ---- 10. Tail drops: Unit tail as statement vs __oxide_ret hoisting ----


def test_unit_tail_becomes_statement_followed_by_drops() -> None:
    rust = _transpile_ok(S1_SOURCE)
    assert "print(&len(&v2));\n    drop(v2);\n}" in rust


def test_value_tail_hoists_into_oxide_ret_before_drops() -> None:
    rust = _transpile_ok(R3_SOURCE)
    assert "let __oxide_ret: i64 = len(&w);\n    drop(w);\n    __oxide_ret\n}" in rust


# ---- Compile checks for the section 25 items that also say "compiles" ----

EDGE_PARAMS = [
    pytest.param("", id="empty"),
    pytest.param(KEYWORD_SOURCE, id="raw-keyword"),
    pytest.param(SHADOW_SOURCE, id="shadow-rename"),
    pytest.param(REF_FORM_SOURCE, id="ref-form"),
]


@requires_rustc
@pytest.mark.parametrize("source", EDGE_PARAMS)
def test_edge_case_emissions_compile(source: str, tmp_path: object) -> None:
    rust = _transpile_ok(source)
    _compile(rust, str(tmp_path))


# ---- 11. CLI behavior of main.py ----


def test_cli_valid_file_writes_rust_to_stdout_and_exits_zero(
    tmp_path: object,
) -> None:
    # Arrange
    ox_file = _write(str(tmp_path), "ok.ox", S1_SOURCE)
    # Act
    proc = subprocess.run(
        ["python3", "main.py", ox_file], capture_output=True, text=True
    )
    # Assert
    assert proc.returncode == 0
    assert "#![allow(dead_code)]" in proc.stdout
    assert "fn main() {" in proc.stdout


def test_cli_diagnostics_render_to_stderr_with_exit_one(tmp_path: object) -> None:
    # Arrange
    ox_file = _write(str(tmp_path), "bad.ox", S9_SOURCE)
    # Act
    proc = subprocess.run(
        ["python3", "main.py", ox_file], capture_output=True, text=True
    )
    # Assert
    assert proc.returncode == 1
    assert "error[OX0300]" in proc.stderr


def test_cli_missing_file_exits_two(tmp_path: object) -> None:
    # Arrange
    missing = str(tmp_path) + "/absent.ox"
    # Act
    proc = subprocess.run(
        ["python3", "main.py", missing], capture_output=True, text=True
    )
    # Assert
    assert proc.returncode == 2
    assert proc.stderr != ""


# ---- 12. transpile never raises on garbage or error-golden sources ----


@pytest.mark.parametrize(
    "source",
    GARBAGE_SOURCES + [S2_SOURCE, S3_SOURCE, S5_SOURCE, S9_SOURCE, S10_SOURCE],
)
def test_transpile_never_raises_and_reports_failure(source: str) -> None:
    # Act
    rust, diags = transpile(source)
    # Assert
    assert rust is None
    assert isinstance(diags, list)
    assert len(diags) > 0


# ---- Section 21 API: emit_rust agrees with transpile; enforces precondition ----


def test_emit_rust_matches_transpile_on_clean_source() -> None:
    # Arrange
    analyzed = analyze(S1_SOURCE)
    assert analyzed.diagnostics == []
    # Act / Assert
    assert emit_rust(analyzed) == _transpile_ok(S1_SOURCE)


def test_emit_rust_raises_value_error_when_diagnostics_present() -> None:
    # Arrange
    analyzed = analyze(S9_SOURCE)
    # Act / Assert
    with pytest.raises(ValueError):
        emit_rust(analyzed)


# ---------------------------------------------------------------------------
# Regression tests: demonstrated clean-transpile / failed-rustc defects.
# Each test pins one fixed defect (section 25 oracle property).
# ---------------------------------------------------------------------------


@requires_rustc
def test_regression_return_value_reading_dropped_local_is_hoisted(
    tmp_path: object,
) -> None:
    # Arrange — the early return's value READS w, which before-return drops
    # destroy; the emission must hoist the value before the drops.
    source = (
        "fn f(c: Bool) -> Int { let w = push(vec(), 1)\n"
        " if c { return len(w) }\n 0 }\n"
        "fn main() { print(f(true))\n print(f(false)) }"
    )
    # Act
    rust = _transpile_ok(source)
    # Assert
    assert "let __oxide_ret: i64 = len(&w);" in rust
    exe = _compile(rust, str(tmp_path))
    assert _run(exe) == "1\n0\n"


@requires_rustc
def test_regression_left_comparison_operand_is_parenthesized(
    tmp_path: object,
) -> None:
    # Arrange — Rust's comparison tier is non-associative: an equal-
    # precedence comparison child on the LEFT must be parenthesized.
    rust = _transpile_ok("fn f(a: Int, b: Int, c: Bool) -> Bool { (a < b) == c }")
    # Assert
    assert "(a < b) == c" in rust
    _compile(rust, str(tmp_path))


@requires_rustc
def test_regression_if_expression_binop_operand_is_parenthesized(
    tmp_path: object,
) -> None:
    # Arrange — a bare block-form if at statement start parses as a
    # statement, orphaning the trailing operator.
    rust = _transpile_ok("fn f(c: Bool) -> Int { (if c { 1 } else { 2 }) + 3 }")
    # Assert
    _compile(rust, str(tmp_path))


@requires_rustc
def test_regression_user_name_spelling_shadow_rename_does_not_collide(
    tmp_path: object,
) -> None:
    # Arrange — user v__2 occupies the name the shadow scheme would give
    # the rebound v; the emitter must skip to v__3.
    source = (
        "fn main() { let v__2 = push(vec(), 10)\n let v = push(vec(), 20)\n"
        " let v = push(v, 30)\n print(len(v__2))\n print(len(v)) }"
    )
    # Act
    rust = _transpile_ok(source)
    # Assert
    assert "let v__3: Vec<i64> = push(v, 30);" in rust
    exe = _compile(rust, str(tmp_path))
    assert _run(exe) == "1\n2\n"


def test_regression_cli_non_utf8_file_reports_and_exits_two(
    tmp_path: object,
) -> None:
    # Arrange — an undecodable file is unreadable (section 21): message +
    # exit 2, never a Python traceback with exit 1.
    path = str(tmp_path) + "/bad.ox"
    with open(path, "wb") as fh:
        fh.write(b"fn main() { \xff\xfe }")
    # Act
    proc = subprocess.run(
        ["python3", "main.py", path], capture_output=True, text=True
    )
    # Assert
    assert proc.returncode == 2
    assert proc.stderr != ""
    assert "Traceback" not in proc.stderr


@requires_rustc
def test_regression_struct_equality_derives_partial_eq(tmp_path: object) -> None:
    # Arrange — == on a struct type needs PartialEq on that struct (only
    # structs actually compared gain the derive; R4 pins Debug, Clone).
    source = (
        "struct P { x: Int, y: Int }\n"
        "fn main() { let a = P { x: 1, y: 2 }\n let b = P { x: 1, y: 2 }\n"
        " print(a == b) }"
    )
    # Act
    rust = _transpile_ok(source)
    # Assert
    assert "#[derive(Debug, Clone, PartialEq)]\nstruct P {" in rust
    exe = _compile(rust, str(tmp_path))
    assert _run(exe) == "true\n"


@requires_rustc
def test_regression_user_variable_named_oxide_ret_is_renamed(
    tmp_path: object,
) -> None:
    # Arrange — __oxide_ret is reserved for the synthesized tail temp; a
    # user binding spelled that way must not collide with the hoist.
    source = (
        "fn f() -> Vec<Int> { let __oxide_ret = push(vec(), 1)\n"
        " let w = push(vec(), 2)\n push(w, len(__oxide_ret)) }"
    )
    # Act
    rust = _transpile_ok(source)
    # Assert
    _compile(rust, str(tmp_path))


@requires_rustc
def test_regression_underscore_variable_emits_usable_identifier(
    tmp_path: object,
) -> None:
    # Arrange — `_` is a legal Oxide identifier but a Rust wildcard
    # pattern; drop(_) would not compile.
    rust = _transpile_ok("fn main() { let _ = push(vec(), 1)\n print(0) }")
    # Assert
    assert "drop(_);" not in rust
    _compile(rust, str(tmp_path))


def test_regression_copy_if_statement_emits_pinned_expression_form() -> None:
    # Arrange — section 22 pins `expr;` for copy-valued ExprStmts; the
    # emitter must not substitute `let _ = if ...;`.
    rust = _transpile_ok("fn f(c: Bool) { if c { 1 } else { 2 }\n print(0) }")
    # Assert
    assert "let _ =" not in rust
    assert "    };" in rust


@requires_rustc
def test_regression_large_int_literal_carries_i64_suffix(
    tmp_path: object,
) -> None:
    # Arrange — unconstrained positions default unsuffixed literals to
    # i32; i64::MAX must emit with an explicit suffix.
    rust = _transpile_ok("fn main() { print(9223372036854775807) }")
    # Assert
    assert "9223372036854775807i64" in rust
    exe = _compile(rust, str(tmp_path))
    assert _run(exe) == "9223372036854775807\n"


@requires_rustc
def test_regression_if_expr_value_feeding_read_arg_compiles(
    tmp_path: object,
) -> None:
    # Arrange — the if-expr arm tails feeding an argument are moves
    # (section 17 value chain); the arms must not also be dropped after
    # the statement. Root cause was upstream in the cfg lowering.
    source = (
        "fn main() { let a = push(vec(), 1)\n let b = vec()\n"
        " let c = false\n print(len(if c { a } else { b })) }"
    )
    # Act
    rust = _transpile_ok(source)
    # Assert
    exe = _compile(rust, str(tmp_path))
    assert _run(exe) == "0\n"


@requires_rustc
def test_regression_struct_literal_condition_is_parenthesized(
    tmp_path: object,
) -> None:
    # Arrange — Rust shares Oxide's no-struct-literal-in-condition rule;
    # the source parens are lost in the AST, so the emitted condition
    # must be wrapped.
    source = (
        "struct Point { x: Int, y: Int }\n"
        "fn main() { if (Point { x: 1, y: 2 }).x > 0 { print(1) } }"
    )
    # Act
    rust = _transpile_ok(source)
    # Assert
    exe = _compile(rust, str(tmp_path))
    assert _run(exe) == "1\n"


@requires_rustc
def test_regression_user_fn_named_drop_does_not_capture_drops(
    tmp_path: object,
) -> None:
    # Arrange — a module-level fn drop shadows std::mem::drop; generated
    # drops must call the std path.
    source = (
        "fn drop(x: Int) -> Int { x }\n"
        "fn main() { let v = push(vec(), 1)\n print(len(v))\n print(drop(5)) }"
    )
    # Act
    rust = _transpile_ok(source)
    # Assert
    assert "std::mem::drop(v);" in rust
    exe = _compile(rust, str(tmp_path))
    assert _run(exe) == "1\n5\n"


@requires_rustc
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            'struct String { x: Int }\nfn main() { let s = "hi"\n print(s) }',
            id="struct-named-String",
        ),
        pytest.param(
            "struct i64 { x: Int }\nfn main() { print(0) }",
            id="struct-named-i64",
        ),
        pytest.param(
            "struct bool { x: Int }\nfn main() { let b = true\n print(b) }",
            id="struct-named-bool",
        ),
    ],
)
def test_regression_struct_named_after_emitted_type_is_renamed(
    source: str, tmp_path: object
) -> None:
    # Arrange / Act — user structs named after emitted Rust type names
    # must not shadow those names.
    rust = _transpile_ok(source)
    # Assert
    _compile(rust, str(tmp_path))


@requires_rustc
def test_regression_constant_division_by_zero_panics_at_runtime(
    tmp_path: object,
) -> None:
    # Arrange — rustc's deny-by-default unconditional_panic lint would
    # reject the accepted program; it must compile and panic at runtime.
    rust = _transpile_ok("fn main() { print(1 / 0) }")
    # Act
    exe = _compile(rust, str(tmp_path))
    proc = subprocess.run([exe], capture_output=True, text=True)
    # Assert
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# §55 `vec(...)` list literal — codegen is untouched: the desugar is purely
# syntactic (parser-level), so a variadic `vec(...)` and its hand-written
# push-chain equivalent must emit the same Rust and run identically.
# ---------------------------------------------------------------------------


def test_variadic_vec_literal_emits_byte_identical_rust_to_the_push_chain() -> None:
    # Arrange
    variadic = "fn main() { for x in vec(3, 8, -2) { print(x) } }"
    chain = "fn main() { for x in push(push(push(vec(), 3), 8), -2) { print(x) } }"
    # Act / Assert — codegen never sees sugar, so the emissions cannot differ.
    assert _transpile_ok(variadic) == _transpile_ok(chain)


@requires_rustc
def test_variadic_vec_literal_runtime_stdout_matches_the_push_chain(
    tmp_path: object,
) -> None:
    # Arrange
    variadic = "fn main() { for x in vec(3, 8, -2) { print(x) } }"
    chain = "fn main() { for x in push(push(push(vec(), 3), 8), -2) { print(x) } }"
    # Act
    variadic_out = _run(_compile(_transpile_ok(variadic), str(tmp_path)))
    chain_out = _run(_compile(_transpile_ok(chain), str(tmp_path)))
    # Assert
    assert variadic_out == chain_out == "3\n8\n-2\n"


# ---- §56 field assignment ----

FA_SOURCE = (
    "struct P { x: Int, y: Int }\n"
    "fn main() { let p = P { x: 1, y: 2 }\n p.x = 5\n print(p.x) }"
)


def test_field_assignment_emits_a_place_write_not_a_clone():
    """§56: the target is a PLACE. Routing it through the field-access
    emitter would append §36's `.clone()` and write into a temporary,
    silently losing the assignment."""
    rust, diags = transpile(FA_SOURCE)
    assert diags == [], diags
    assert "p.x = 5;" in rust
    assert "p.x.clone() = " not in rust


def test_field_assigned_binding_is_emitted_mut():
    """The base joins the `assigned` set via assign_of, so `mut` falls out
    of the existing inference."""
    rust, diags = transpile(FA_SOURCE)
    assert diags == [], diags
    assert "let mut p: P = " in rust


@requires_rustc
def test_field_assignment_compiles_under_rustc(tmp_path):
    """Accepted-implies-compiles."""
    rust, diags = transpile(FA_SOURCE)
    assert diags == [], diags
    src = tmp_path / "prog.rs"
    src.write_text(rust, encoding="utf-8")
    proc = subprocess.run(
        [RUSTC, "--edition", "2021", "-o", str(tmp_path / "prog"), str(src)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
