"""Blind TDD tests for Phase 3 semantic analysis: resolve + infer + modes.

Covers SPEC.md section 20's first test-file plan (items 1-12), asserting the
section 19 golden outputs it references (S1, S3, S4, S7-S10). Imports only
the ``src.sema.analyze`` surface, per the blind-test contract.
"""

from __future__ import annotations

import pytest

from src.sema.analyze import (
    analyze,
    diag_codes,
    drop_list,
    param_modes,
    use_classes,
    var_types_by_name,
)

# ---------------------------------------------------------------------------
# Helpers & golden sources (SPEC.md section 19)
# ---------------------------------------------------------------------------


def codes(src: str) -> list[str]:
    """Run the full pipeline and return the diagnostic code list."""
    return diag_codes(analyze(src))


S1_SRC = "fn main() { let v = vec()\n let v2 = push(v, 1)\n print(len(v2)) }"

S3_SRC = (
    "fn f(v: Vec<Int>) -> Vec<Int> { let a = push(v, 1)\n let b = push(v, 2)\n a }"
)

S4_SRC = "fn g(c: Bool, v: Vec<Int>) { if c { let w = push(v, 1) } }"

S7_SRC = (
    "struct Point { x: Int, y: Int }\n"
    "fn area(p: Point) -> Int { let Point { x, y } = p\n x * y }"
)

S8_SRC = (
    "fn wrap(v: Vec<Int>) -> Vec<Int> { push(v, 1) }\n"
    "fn caller(v: Vec<Int>) { let w = wrap(v)\n print(len(w)) }"
)

S9_SRC = "fn bad() { let x = 1 + true }"

S10_SRC = "fn f() { print(g)\n print(len) }"


# ---------------------------------------------------------------------------
# Item 1 — goldens S7-S10
# ---------------------------------------------------------------------------


def test_s7_destructured_param_is_owned_and_fields_are_copy() -> None:
    # Arrange / Act
    res = analyze(S7_SRC)

    # Assert — S7 golden, exactly as stated
    assert diag_codes(res) == []
    assert param_modes(res, "area") == ("own",)
    assert use_classes(res, "area", "x") == ["copy"]
    assert var_types_by_name(res, "area", "p") == ["Point"]
    assert drop_list(res) == []


def test_s8_ownership_flows_through_user_function_chain() -> None:
    # Arrange / Act
    res = analyze(S8_SRC)

    # Assert — S8 golden
    assert diag_codes(res) == []
    assert param_modes(res, "wrap") == ("own",)
    assert param_modes(res, "caller") == ("own",)
    assert drop_list(res) == [("caller", "w", "after-stmt")]


def test_s9_int_plus_bool_mismatch_suppresses_drops() -> None:
    # Arrange / Act
    res = analyze(S9_SRC)

    # Assert — S9 golden
    assert diag_codes(res) == ["OX0300"]
    assert drop_list(res) == []


def test_s10_unknown_name_then_builtin_as_value_in_source_order() -> None:
    # Arrange / Act / Assert — S10 golden
    assert codes(S10_SRC) == ["OX0200", "OX0201"]


# ---------------------------------------------------------------------------
# Item 2 — literal typing; param inference from body and from call site
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        pytest.param("1", "Int", id="int"),
        pytest.param("1.5", "Float", id="float"),
        pytest.param('"s"', "Str", id="str"),
        pytest.param("true", "Bool", id="true"),
        pytest.param("false", "Bool", id="false"),
    ],
)
def test_literals_type_directly(literal: str, expected: str) -> None:
    # Arrange
    src = f"fn m() {{ let a = {literal} }}"

    # Act
    res = analyze(src)

    # Assert
    assert diag_codes(res) == []
    assert var_types_by_name(res, "m", "a") == [expected]


def test_unannotated_param_inferred_from_body_usage() -> None:
    # Arrange / Act
    res = analyze("fn double(x) -> Int { x + x }")

    # Assert
    assert diag_codes(res) == []
    assert var_types_by_name(res, "double", "x") == ["Int"]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("fn f(x) { x + x }", id="binop-on-param"),
        pytest.param("fn f(x) { -x }", id="unop-on-param"),
    ],
)
def test_unconstrained_param_tied_to_return_is_ambiguous_not_unit(
    src: str,
) -> None:
    # Arrange / Act — x is unconstrained by unification; return-type
    # Unit-defaulting must not fabricate 'Unit' for it (regression).
    res = analyze(src)

    # Assert — OX0302 at the binding, type becomes Error (SPEC section 15/16)
    assert diag_codes(res) == ["OX0302"]
    assert var_types_by_name(res, "f", "x") == ["Error"]


def test_unannotated_param_inferred_from_call_site_across_functions() -> None:
    # Arrange — print constrains nothing; only the cross-fn call pins x
    src = "fn h(x) { print(x) }\nfn main() { h(1) }"

    # Act
    res = analyze(src)

    # Assert
    assert diag_codes(res) == []
    assert var_types_by_name(res, "h", "x") == ["Int"]


# ---------------------------------------------------------------------------
# Item 3 — vec/push/len chain types (S1's var_types_by_name)
# ---------------------------------------------------------------------------


def test_vec_push_len_chain_infers_vec_int() -> None:
    # Arrange / Act
    res = analyze(S1_SRC)

    # Assert — S1 golden types
    assert diag_codes(res) == []
    assert var_types_by_name(res, "main", "v") == ["Vec<Int>"]
    assert var_types_by_name(res, "main", "v2") == ["Vec<Int>"]


# ---------------------------------------------------------------------------
# Item 4 — if-expression unification
# ---------------------------------------------------------------------------


def test_if_arms_unify_to_the_common_type() -> None:
    # Arrange
    src = "fn f(c: Bool) { let m = if c { 1 } else { 2 }\n print(m) }"

    # Act
    res = analyze(src)

    # Assert
    assert diag_codes(res) == []
    assert var_types_by_name(res, "f", "m") == ["Int"]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param(
            "fn f(c: Bool) { let m = if c { 1 } else { true }\n print(m) }",
            id="arm-mismatch",
        ),
        pytest.param("fn f(c: Bool) { if c { 1 } }", id="missing-else-non-unit-then"),
        pytest.param("fn f() { if 1 { } }", id="non-bool-condition"),
    ],
)
def test_ill_typed_if_expressions_report_ox0300(src: str) -> None:
    # Arrange / Act / Assert
    assert codes(src) == ["OX0300"]


def test_loop_body_with_non_unit_tail_reports_ox0300() -> None:
    """Regression: infer discarded the loop-body block type instead of
    unifying it with Unit (the While arm of ``_expr`` and ``_for`` both
    dropped the ``_block(body)`` result), so ``while c { 1 }`` and
    ``for x in v { x }`` analyzed clean yet emitted Rust that fails
    E0308 — Rust loop body blocks must be ``()``."""
    # Arrange / Act / Assert
    assert codes("fn f(c: Bool) { while c { 1 } }") == ["OX0300"]
    assert codes("fn f(v: Vec<Int>) { for x in v { x } }") == ["OX0300"]


# ---------------------------------------------------------------------------
# Item 5 — operators
# ---------------------------------------------------------------------------


def test_int_modulo_int_is_int() -> None:
    # Arrange / Act
    res = analyze("fn f() { let x = 1 % 2 }")

    # Assert
    assert diag_codes(res) == []
    assert var_types_by_name(res, "f", "x") == ["Int"]


def test_equality_on_vec_operands_yields_bool() -> None:
    # Arrange
    src = "fn f(a: Vec<Int>, b: Vec<Int>) { let r = a == b\n print(r) }"

    # Act
    res = analyze(src)

    # Assert — == is legal on any unifying operands, result Bool
    assert diag_codes(res) == []
    assert var_types_by_name(res, "f", "r") == ["Bool"]


@pytest.mark.parametrize(
    ("src", "code"),
    [
        pytest.param("fn f() { let x = true + false }", "OX0305", id="bool-plus-bool"),
        pytest.param("fn f() { let x = 1.5 % 2.0 }", "OX0305", id="float-modulo"),
        pytest.param("fn f() { let x = 1 < 1.5 }", "OX0300", id="mixed-comparison"),
        pytest.param("fn f() { let x = !1 }", "OX0300", id="not-on-int"),
    ],
)
def test_operator_misuse_reports_expected_code(src: str, code: str) -> None:
    # Arrange / Act / Assert
    assert codes(src) == [code]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("fn f() { let x = true + 1\n print(0) }", id="bool-plus-int"),
        pytest.param("fn f() { let x = true < 1\n print(0) }", id="bool-lt-int"),
        pytest.param("fn f() { let x = 1.5 % 2\n print(0) }", id="float-mod-int"),
    ],
)
def test_operand_mismatch_reports_one_code_regardless_of_order(src: str) -> None:
    # Arrange / Act / Assert — one unification failure must yield exactly
    # one OX0300, with no operand-order-dependent OX0305 cascade (regression)
    assert codes(src) == ["OX0300"]


# ---------------------------------------------------------------------------
# Item 6 — struct shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "code"),
    [
        pytest.param(
            "struct P { x: Int }\nfn f() { let p = P { x: true } }",
            "OX0300",
            id="field-type-mismatch",
        ),
        pytest.param(
            "struct P { x: Int, y: Int }\nfn f() { let p = P { x: 1 } }",
            "OX0304",
            id="missing-literal-field",
        ),
        pytest.param(
            "struct P { x: Int }\nfn f() { let p = P { x: 1, z: 2 } }",
            "OX0304",
            id="extra-literal-field",
        ),
        pytest.param(
            "struct P { x: Int }\nfn f() { let p = P { x: 1, x: 2 } }",
            "OX0304",
            id="duplicate-literal-field",
        ),
        pytest.param(
            "struct P { x: Int, y: Int }\nfn f(p: P) { let P { x } = p }",
            "OX0304",
            id="incomplete-destructure",
        ),
        pytest.param(
            "fn f() { let p = Q { x: 1 } }",
            "OX0202",
            id="unknown-struct",
        ),
        pytest.param(
            "struct P { x: Int }\nfn f(p: P) { let b = p.z }",
            "OX0304",
            id="unknown-field-access",
        ),
        pytest.param(
            "fn f(a: Int) { let b = a.x }",
            "OX0306",
            id="field-access-on-int",
        ),
    ],
)
def test_struct_shape_violations_report_expected_code(src: str, code: str) -> None:
    # Arrange / Act / Assert
    assert codes(src) == [code]


def test_user_struct_named_error_is_a_distinct_nominal_type() -> None:
    # Arrange — 'Error' must not alias the internal error sentinel: a
    # struct-to-Int coercion is a plain OX0300, never silent (regression)
    coerce_src = "struct Error { }\nfn coerce(x: Error) -> Int { x }"
    bind_src = "struct Error { }\nfn f() { let x: Error = 123 }"

    # Act / Assert
    assert codes(coerce_src) == ["OX0300"]
    assert codes(bind_src) == ["OX0300"]


# ---------------------------------------------------------------------------
# Item 7 — type annotations
# ---------------------------------------------------------------------------


def test_matching_vec_annotation_pins_the_element_type() -> None:
    # Arrange / Act
    res = analyze("fn f() { let x: Vec<Int> = vec() }")

    # Assert
    assert diag_codes(res) == []
    assert var_types_by_name(res, "f", "x") == ["Vec<Int>"]


@pytest.mark.parametrize(
    ("src", "code"),
    [
        pytest.param("fn f() { let x: Int = 1.5 }", "OX0300", id="annotation-mismatch"),
        pytest.param("fn f() { let x: Foo = 1 }", "OX0202", id="unknown-type-name"),
        pytest.param(
            "fn f() { let x: Vec<Int, Int> = vec() }", "OX0202", id="vec-wrong-arity"
        ),
        pytest.param("fn f() { let x: Int<Int> = 1 }", "OX0202", id="int-takes-no-args"),
    ],
)
def test_bad_annotations_report_expected_code(src: str, code: str) -> None:
    # Arrange / Act / Assert
    assert codes(src) == [code]


# ---------------------------------------------------------------------------
# Item 8 — ambiguous type
# ---------------------------------------------------------------------------


def test_unconstrained_vec_element_is_ambiguous() -> None:
    # Arrange / Act / Assert
    assert codes("fn f() { let v = vec() }") == ["OX0302"]


# ---------------------------------------------------------------------------
# Item 9 — arity / not callable
# ---------------------------------------------------------------------------


def test_len_with_no_arguments_reports_wrong_arg_count() -> None:
    # Arrange / Act / Assert — membership: the dangling generic may also report
    assert "OX0303" in codes("fn f() { len() }")


def test_calling_an_int_local_reports_not_callable() -> None:
    # Arrange / Act / Assert
    assert codes("fn f() { let a = 1\n a(1) }") == ["OX0303"]


# ---------------------------------------------------------------------------
# Item 10 — resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "code"),
    [
        pytest.param("fn f() { print(zzz) }", "OX0200", id="unknown-identifier"),
        pytest.param("fn f() { print(len) }", "OX0201", id="builtin-as-value"),
        pytest.param("fn f() { }\nfn f() { }", "OX0203", id="duplicate-fn"),
        pytest.param("fn print() { }", "OX0203", id="fn-clashes-with-builtin"),
        pytest.param("fn f(a: Int, a: Int) { }", "OX0204", id="duplicate-param"),
        pytest.param(
            "struct P { x: Int, y: Int }\nfn f(p: P) { let P { x, x } = p }",
            "OX0204",
            id="duplicate-destructure-binder",
        ),
    ],
)
def test_resolution_errors_report_expected_code(src: str, code: str) -> None:
    # Arrange / Act / Assert
    assert codes(src) == [code]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param(
            "struct Int { v: Bool }\nfn main() { let a = Int { v: true }\n"
            " let b = a + 1\n print(b) }",
            id="struct-int",
        ),
        pytest.param(
            "struct Bool { }\nfn main() { let b = Bool { }\n"
            " let c = b && true\n print(c) }",
            id="struct-bool",
        ),
    ],
)
def test_struct_shadowing_a_primitive_type_name_is_rejected(src: str) -> None:
    # Arrange / Act / Assert — a struct literal must never inhabit a
    # primitive type; the reserved name reports OX0203 (regression)
    assert codes(src) == ["OX0203"]


def test_shadowing_rebinds_the_name_with_independent_types() -> None:
    # Arrange
    src = "fn f() { let x = 1\n print(x)\n let x = true\n print(x) }"

    # Act
    res = analyze(src)

    # Assert — two bindings, binding order, independent types
    assert diag_codes(res) == []
    assert var_types_by_name(res, "f", "x") == ["Int", "Bool"]


# ---------------------------------------------------------------------------
# Item 11 — parameter modes
# ---------------------------------------------------------------------------


def test_s3_double_move_of_param_is_own_with_drops_suppressed() -> None:
    # Arrange / Act
    res = analyze(S3_SRC)

    # Assert — S3 golden
    assert diag_codes(res) == ["OX0401"]
    assert param_modes(res, "f") == ("own",)
    assert drop_list(res) == []


def test_s4_conditional_move_makes_param_own_with_hoisted_drops() -> None:
    # Arrange / Act
    res = analyze(S4_SRC)

    # Assert — S4 golden
    assert diag_codes(res) == []
    assert param_modes(res, "g") == ("read", "own")
    assert drop_list(res) == [("g", "v", "branch-end"), ("g", "w", "block-end")]


@pytest.mark.parametrize(
    ("src", "fn_name", "expected"),
    [
        pytest.param(S8_SRC, "wrap", ("own",), id="s8-wrap-own"),
        pytest.param(S8_SRC, "caller", ("own",), id="s8-caller-own"),
        pytest.param(S7_SRC, "area", ("own",), id="s7-destructured-param-own"),
        pytest.param(
            "fn f(a: Int) -> Int { a }", "f", ("read",), id="returned-copy-param-read"
        ),
        pytest.param(
            "fn r(v: Vec<Int>) { r(v) }", "r", ("read",), id="pure-recursion-read"
        ),
    ],
)
def test_param_modes_follow_ownership_evidence(
    src: str, fn_name: str, expected: tuple[str, ...]
) -> None:
    # Arrange / Act / Assert
    assert param_modes(analyze(src), fn_name) == expected


def test_move_in_unreachable_code_does_not_flip_param_mode() -> None:
    # Arrange — the move of v happens only after an unconditional return,
    # so no execution path uses v in a MOVE context (regression)
    dead_src = "fn dead(v: Vec<Int>) { return\nlet x = v }"
    caller_src = dead_src + "\nfn caller(v: Vec<Int>) { dead(v)\nprint(len(v)) }"

    # Act / Assert — 'read' mode, and the clean caller reports nothing
    assert param_modes(analyze(dead_src), "dead") == ("read",)
    assert codes(caller_src) == []


# ---------------------------------------------------------------------------
# Item 12 — gates & robustness
# ---------------------------------------------------------------------------


def test_resolve_error_gates_infer_diagnostics() -> None:
    # Arrange — zzz is unknown AND zzz + true would be a type error;
    # only the resolve code may surface (infer is skipped).
    src = "fn f() { let x = zzz + true }"

    # Act / Assert
    assert codes(src) == ["OX0200"]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("fn f() { let = 5 }", id="bad-let-pattern"),
        pytest.param("42\nfn g() { }", id="expr-at-module-level"),
    ],
)
def test_parse_error_yields_only_lex_and_parse_codes(src: str) -> None:
    # Arrange / Act
    res = analyze(src)
    got = diag_codes(res)

    # Assert — something reported, nothing from sema (all codes < OX0200),
    # and a skipped pipeline contributes no drops.
    assert got
    assert all(int(c[2:]) < 200 for c in got)
    assert drop_list(res) == []


@pytest.mark.parametrize(
    "src",
    [
        pytest.param('"\\u{', id="unterminated-unicode-escape"),
        pytest.param("/*/*/*", id="nested-comment-soup"),
        pytest.param("\x00\xff@#$", id="control-bytes"),
        pytest.param("0x 0b2 9e", id="bad-numbers"),
        pytest.param("fn", id="bare-fn"),
        pytest.param("fn f(", id="unclosed-params"),
        pytest.param("{", id="lone-lbrace"),
        pytest.param("}}}", id="stray-rbraces"),
        pytest.param("fn f() -> {", id="missing-return-type"),
    ],
)
def test_analyze_never_raises_on_garbage_inputs(src: str) -> None:
    # Arrange / Act — the only requirement is that analyze returns normally
    res = analyze(src)
    got = diag_codes(res)

    # Assert — helpers stay usable on the result
    assert isinstance(got, list)
    assert all(isinstance(c, str) for c in got)


# ---------------------------------------------------------------------------
# Regression: defects demonstrated against the section 25 oracle property
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("fn main(x: Int) { print(x) }", id="main-with-param"),
        pytest.param("fn main() -> Int { 0 }", id="main-with-return"),
    ],
)
def test_fn_main_must_take_no_params_and_return_unit(src: str) -> None:
    # Arrange / Act — main becomes the Rust entry point (E0580/E0277 if
    # emitted with params or a non-Unit return), so sema must reject it.
    got = codes(src)

    # Assert
    assert got == ["OX0300"]


def test_int_literal_beyond_i64_range_is_rejected() -> None:
    # Arrange — i64::MAX + far more; previously accepted with zero
    # diagnostics and emitted as an out-of-range Rust literal.
    src = "fn main() { let x = 99999999999999999999999999\n print(x) }"

    # Act / Assert
    assert codes(src) == ["OX0300"]
