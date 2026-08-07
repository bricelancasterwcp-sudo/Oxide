"""Blind tests for language v0.2 front end — SPEC.md Part V, section 31 first file.

Covers all ten section-31 plan items: new keywords (item 1), parse dumps for
the new constructs (item 2), the section-30 front-end goldens V1-V7 (item 3),
the match shape matrix (item 4), variant namespace rules (item 5), assignment
semantics (item 6), Option/Result typing (item 7), for-loop semantics
(item 8), new builtin modes (item 9), and never-raises garbage (item 10).

Authored blind from SPEC.md alone; the implementation lands concurrently.
"""

from __future__ import annotations

import pytest

from src.lexer.lexer import Lexer
from src.lexer.tokens import TokenKind
from src.parser.ast import dump
from src.parser.parser import parse_source
from src.sema.analyze import (
    analyze,
    diag_codes,
    drop_list,
    param_modes,
    var_types_by_name,
)

K = TokenKind

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def kinds(src: str) -> list[TokenKind]:
    """Token kind sequence for ``src`` (always EOF-terminated)."""
    return [tok.kind for tok in Lexer(src).tokenize()]


def d_clean(src: str) -> str:
    """Canonical dump of ``src``, asserting a diagnostic-free parse."""
    module, diags = parse_source(src)
    assert diags == []
    return dump(module)


def codes_of(src: str) -> list[str]:
    """Phase-ordered diagnostic codes from a full analyze of ``src``."""
    return diag_codes(analyze(src))


def fn_mod(
    body: str,
    params: str = "(params)",
    ret: str | None = None,
    name: str = "f",
) -> str:
    """Expected module dump for a single fn wrapping ``body``."""
    ret_part = f" {ret}" if ret else ""
    return f"(module (fn {name} {params}{ret_part} {body}))"


# ---------------------------------------------------------------------------
# Shared sources (SPEC section 30, verbatim where pinned)
# ---------------------------------------------------------------------------

SHAPE_ENUM = (
    "enum Shape {\n"
    "    Circle(Float),\n"
    "    Rect(Float, Float),\n"
    "    Empty,\n"
    "}\n"
    "\n"
)

COLOR_ENUM = (
    "enum Color {\n"
    "    Red,\n"
    "    Blue,\n"
    "}\n"
    "\n"
)

R5_SRC = SHAPE_ENUM + (
    "fn describe(s: Shape) -> Float {\n"
    "    match s {\n"
    "        Circle(r) => r * r,\n"
    "        Rect(w, h) => w * h,\n"
    "        Empty => 0.0,\n"
    "    }\n"
    "}\n"
    "\n"
    "fn main() {\n"
    "    print(describe(Rect(3.0, 4.0)))\n"
    "}\n"
)

R6_SRC = (
    "fn sum_squares(n: Int) -> Int {\n"
    "    let total = 0\n"
    "    for i in range(0, n) {\n"
    "        total = total + i * i\n"
    "    }\n"
    "    total\n"
    "}\n"
    "\n"
    "fn main() {\n"
    "    print(sum_squares(5))\n"
    "}\n"
)


# ---------------------------------------------------------------------------
# Item 1 — new keywords lex as KW_FOR / KW_IN / KW_ENUM (SPEC section 26)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "kw"),
    [
        pytest.param("for", K.KW_FOR, id="for"),
        pytest.param("in", K.KW_IN, id="in"),
        pytest.param("enum", K.KW_ENUM, id="enum"),
    ],
)
def test_new_keywords_lex_as_keyword_kinds(src: str, kw: TokenKind) -> None:
    # Keywords are not terminators, so no NEWLINE is injected before EOF.
    assert kinds(src) == [kw, K.EOF]


@pytest.mark.parametrize(
    "src", ["forx", "format", "int", "into", "enumx", "enumerate"]
)
def test_keyword_prefixed_words_stay_ident(src: str) -> None:
    # Maximal munch: only exact matches are keywords; IDENT is a terminator,
    # hence the injected NEWLINE before EOF.
    assert kinds(src) == [K.IDENT, K.NEWLINE, K.EOF]


def test_for_in_header_token_stream() -> None:
    assert kinds("for x in xs") == [
        K.KW_FOR, K.IDENT, K.KW_IN, K.IDENT, K.NEWLINE, K.EOF,
    ]


# ---------------------------------------------------------------------------
# Item 2 — parse dumps for enum / match / for / assignment (sections 26-27)
# ---------------------------------------------------------------------------


def test_enum_decl_dump_payload_nullary_and_trailing_comma() -> None:
    assert d_clean(SHAPE_ENUM) == (
        "(module (enum Shape (variant Circle (type Float)) "
        "(variant Rect (type Float) (type Float)) (variant Empty)))"
    )


def test_enum_decl_dump_single_line_all_nullary_no_trailing_comma() -> None:
    assert d_clean("enum Color { Red, Blue }") == (
        "(module (enum Color (variant Red) (variant Blue)))"
    )


MATCH_PARSE_SRC = (
    "fn f(s: Shape) -> Int {\n"
    "    match s {\n"
    "        Circle(r) => 1,\n"
    "        Rect(w, h) => {\n"
    "            2\n"
    "        },\n"
    "        _ => 3,\n"
    "    }\n"
    "}\n"
)


def test_match_dump_expr_arm_block_arm_and_wildcard() -> None:
    match_dump = (
        "(match (var s) (arm (vpat Circle r) (lit int 1)) "
        "(arm (vpat Rect w h) (block (tail (lit int 2)))) "
        "(arm (vpat _) (lit int 3)))"
    )
    assert d_clean(MATCH_PARSE_SRC) == fn_mod(
        f"(block (tail {match_dump}))",
        params="(params (param s (type Shape)))",
        ret="(ret (type Int))",
    )


def test_for_stmt_wrapped_in_exprstmt_and_excluded_from_tail() -> None:
    # The for is the last statement before `}` yet must NOT become the tail.
    body = (
        "(block (exprstmt (for x (var xs) "
        "(block (tail (call (var print) (var x)))))))"
    )
    assert d_clean("fn f() { for x in xs { print(x) } }") == fn_mod(body)


def test_assignment_dump_is_a_statement() -> None:
    assert d_clean("fn f() { x = 1 }") == fn_mod(
        "(block (assign x (lit int 1)))"
    )


def test_assignment_dump_with_call_rhs() -> None:
    assert d_clean("fn f() { acc = push(acc, 1) }") == fn_mod(
        "(block (assign acc (call (var push) (var acc) (lit int 1))))"
    )


def test_eqeq_still_parses_as_comparison_exprstmt() -> None:
    # Lookahead is IDENT EQ, not IDENT EQEQ: `x == y` stays an expression.
    assert d_clean("fn f() { x == y\n 1 }") == fn_mod(
        "(block (exprstmt (bin == (var x) (var y))) (tail (lit int 1)))"
    )


# ---------------------------------------------------------------------------
# Item 3 — front-end goldens V1-V7 (SPEC section 30, exact)
# ---------------------------------------------------------------------------


def test_v1_r5_enum_match_types_and_modes() -> None:
    res = analyze(R5_SRC)
    assert diag_codes(res) == []
    assert param_modes(res, "describe") == ("own",)
    assert var_types_by_name(res, "describe", "s") == ["Shape"]
    assert var_types_by_name(res, "describe", "r") == ["Float"]


def test_v2_r6_for_and_assignment_accumulation() -> None:
    res = analyze(R6_SRC)
    assert diag_codes(res) == []
    assert param_modes(res, "sum_squares") == ("read",)


def test_v3_for_over_read_param_no_drops() -> None:
    res = analyze("fn f(v: Vec<Int>) { for x in v { print(x) } }")
    assert diag_codes(res) == []
    assert param_modes(res, "f") == ("read",)
    assert drop_list(res) == []


V4_SRC = SHAPE_ENUM + (
    "fn describe(s: Shape) -> Float {\n"
    "    match s {\n"
    "        Circle(r) => r * r,\n"
    "        Rect(w, h) => w * h,\n"
    "    }\n"
    "}\n"
)


def test_v4_non_exhaustive_match_reports_ox0307() -> None:
    assert codes_of(V4_SRC) == ["OX0307"]


def test_v5_option_match_types() -> None:
    res = analyze(
        "fn f(o: Option<Int>) -> Int { match o { Some(x) => x, None => 0 } }"
    )
    assert diag_codes(res) == []
    assert var_types_by_name(res, "f", "o") == ["Option<Int>"]


def test_v6_arm_move_of_outer_vec_then_use_reports_ox0400() -> None:
    src = (
        "fn f(o: Option<Int>) {\n"
        "    let v = push(vec(), 1)\n"
        "    match o {\n"
        "        Some(x) => { let w = v },\n"
        "        None => { },\n"
        "    }\n"
        "    print(len(v))\n"
        "}\n"
    )
    res = analyze(src)
    assert diag_codes(res) == ["OX0400"]
    # Section 16: OX0400 carries at least one note pointing at the move.
    assert res.diagnostics[0].notes


def test_v7_assignment_reinitializes_moved_var() -> None:
    src = (
        "fn f() { let v = push(vec(), 1)\n let w = v\n"
        " v = push(vec(), 2)\n print(len(v))\n print(len(w)) }"
    )
    assert codes_of(src) == []


# ---------------------------------------------------------------------------
# Item 4 — match shape matrix, every violation is OX0307 (section 28)
# ---------------------------------------------------------------------------


MATCH_MATRIX = [
    pytest.param(V4_SRC, id="non-exhaustive"),
    pytest.param(
        SHAPE_ENUM
        + "fn f(s: Shape) -> Float {\n"
        "    match s {\n"
        "        Circle(r) => r,\n"
        "        Circle(q) => q,\n"
        "        Rect(w, h) => w,\n"
        "        Empty => 0.0,\n"
        "    }\n"
        "}\n",
        id="duplicate-arm",
    ),
    pytest.param(
        SHAPE_ENUM + COLOR_ENUM
        + "fn f(s: Shape) -> Float {\n"
        "    match s {\n"
        "        Circle(r) => r,\n"
        "        Rect(w, h) => w,\n"
        "        Empty => 0.0,\n"
        "        Red => 1.0,\n"
        "    }\n"
        "}\n",
        id="arm-from-wrong-enum",
    ),
    pytest.param(
        SHAPE_ENUM
        + "fn f(s: Shape) -> Float {\n"
        "    match s {\n"
        "        Circle(r) => r,\n"
        "        Rect(w) => w,\n"
        "        Empty => 0.0,\n"
        "    }\n"
        "}\n",
        id="wrong-binder-arity",
    ),
    pytest.param(
        SHAPE_ENUM
        + "fn f(s: Shape) -> Float {\n"
        "    match s {\n"
        "        _ => 0.0,\n"
        "        Empty => 1.0,\n"
        "    }\n"
        "}\n",
        id="unreachable-arm-after-wildcard",
    ),
    pytest.param(
        "fn f() -> Int {\n"
        "    match 1 {\n"
        "        _ => 0,\n"
        "    }\n"
        "}\n",
        id="match-on-int",
    ),
]


@pytest.mark.parametrize("src", MATCH_MATRIX)
def test_match_shape_violation_reports_ox0307(src: str) -> None:
    assert codes_of(src) == ["OX0307"]


# ---------------------------------------------------------------------------
# Item 5 — variant namespace (section 28)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        pytest.param(
            "struct Circle {\n    r: Float,\n}\n\n"
            "enum Shape {\n    Circle(Float),\n    Empty,\n}\n",
            id="variant-vs-struct",
        ),
        pytest.param(
            "fn Red() { }\n\nenum Color {\n    Red,\n    Blue,\n}\n",
            id="variant-vs-fn",
        ),
        pytest.param(
            "enum MyOpt {\n    Some(Int),\n    Nothing,\n}\n",
            id="variant-vs-reserved-some",
        ),
    ],
)
def test_variant_namespace_collision_reports_ox0203(src: str) -> None:
    assert codes_of(src) == ["OX0203"]


def test_unknown_variant_in_arm_reports_ox0307() -> None:
    src = SHAPE_ENUM + (
        "fn f(s: Shape) -> Float {\n"
        "    match s {\n"
        "        Circle(r) => r,\n"
        "        Rect(w, h) => w,\n"
        "        Empty => 0.0,\n"
        "        Bogus => 1.0,\n"
        "    }\n"
        "}\n"
    )
    assert codes_of(src) == ["OX0307"]


def test_unknown_bare_variant_value_reports_ox0200() -> None:
    assert codes_of("fn f() { print(Bogus) }") == ["OX0200"]


def test_nullary_variant_called_reports_ox0303() -> None:
    assert codes_of(COLOR_ENUM + "fn f() { let x = Red() }\n") == ["OX0303"]


def test_payload_variant_used_bare_reports_ox0303() -> None:
    assert codes_of(SHAPE_ENUM + "fn f() { let x = Circle }\n") == ["OX0303"]


# ---------------------------------------------------------------------------
# Item 6 — assignment semantics (section 28)
# ---------------------------------------------------------------------------


def test_assign_to_unknown_target_reports_ox0200() -> None:
    assert codes_of("fn f() { x = 1 }") == ["OX0200"]


def test_assign_type_mismatch_reports_ox0300() -> None:
    assert codes_of("fn f() { let x = 1\n x = true }") == ["OX0300"]


def test_assigned_param_gets_mode_own() -> None:
    res = analyze("fn f(v: Vec<Int>) { v = push(v, 1) }")
    assert diag_codes(res) == []
    assert param_modes(res, "f") == ("own",)


ACC_WITH_ASSIGN = (
    "fn f() { let acc = push(vec(), 1)\n"
    " while true { acc = push(acc, 1) }\n"
    " print(len(acc)) }"
)

ACC_WITHOUT_ASSIGN = (
    "fn f() { let acc = push(vec(), 1)\n"
    " while true { let b = push(acc, 1) }\n"
    " print(len(acc)) }"
)


def test_loop_reassignment_reestablishes_ownership_no_ox0403() -> None:
    assert codes_of(ACC_WITH_ASSIGN) == []


def test_same_loop_without_assignment_reports_ox0403() -> None:
    assert codes_of(ACC_WITHOUT_ASSIGN) == ["OX0403"]


# ---------------------------------------------------------------------------
# Item 7 — Option / Result (section 28)
# ---------------------------------------------------------------------------


def test_parse_int_returns_option_int() -> None:
    src = (
        "fn f(s: Str) {\n"
        "    let o = parse_int(s)\n"
        "    match o {\n"
        "        Some(x) => print(x),\n"
        "        None => print(0),\n"
        "    }\n"
        "}\n"
    )
    res = analyze(src)
    assert diag_codes(res) == []
    assert var_types_by_name(res, "f", "o") == ["Option<Int>"]
    assert var_types_by_name(res, "f", "x") == ["Int"]


def test_get_returns_option_of_element_type() -> None:
    src = (
        "fn g(v: Vec<Int>) {\n"
        "    let o = get(v, 0)\n"
        "    match o {\n"
        "        Some(x) => print(x),\n"
        "        None => print(0),\n"
        "    }\n"
        "}\n"
    )
    res = analyze(src)
    assert diag_codes(res) == []
    assert var_types_by_name(res, "g", "o") == ["Option<Int>"]


def test_bare_none_alone_is_ambiguous_ox0302() -> None:
    assert codes_of("fn f() { let x = None }") == ["OX0302"]


def test_ok_and_err_arms_unify() -> None:
    src = (
        "fn f(r: Result<Int, Str>) -> Int {\n"
        "    match r {\n"
        "        Ok(x) => x,\n"
        "        Err(e) => str_len(e),\n"
        "    }\n"
        "}\n"
    )
    res = analyze(src)
    assert diag_codes(res) == []
    assert var_types_by_name(res, "f", "r") == ["Result<Int, Str>"]
    assert var_types_by_name(res, "f", "e") == ["Str"]


# ---------------------------------------------------------------------------
# Item 8 — for loops (section 28)
# ---------------------------------------------------------------------------


def test_for_over_non_vec_iterable_reports_ox0300() -> None:
    assert codes_of("fn f() { for x in 1 { print(x) } }") == ["OX0300"]


def test_loop_var_is_fresh_per_iteration_moving_it_is_legal() -> None:
    src = (
        "fn f(vs: Vec<Vec<Int>>) {\n"
        "    for v in vs {\n"
        "        let w = v\n"
        "        print(len(w))\n"
        "    }\n"
        "}\n"
    )
    assert codes_of(src) == []


def test_iterable_var_stays_usable_after_loop() -> None:
    src = (
        "fn f() { let v = push(vec(), 1)\n"
        " for x in v { print(x) }\n"
        " print(len(v)) }"
    )
    res = analyze(src)
    assert diag_codes(res) == []
    assert drop_list(res) == [("f", "v", "after-stmt")]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("v = push(vec(), x)", id="assign"),
        pytest.param("let w = v\n v = push(vec(), x)", id="move-then-reassign"),
        pytest.param("while true { v = push(vec(), x) }", id="assign-in-nested-while"),
    ],
)
def test_assigning_or_moving_the_iterated_var_reports_ox0406(body: str) -> None:
    """Regression: cfg modeled the for iterable as a one-shot pre-loop
    READ and assignment as pure re-init, so a body assigning (or moving
    then reassigning) the iterated variable analyzed clean — yet
    codegen's ``v.iter().cloned()`` borrows ``v`` for the whole loop
    and the emitted Rust failed borrowck (E0506/E0505)."""
    src = (
        "fn main() { let v = push(vec(), 1)\n"
        " for x in v { " + body + " }\n"
        " print(len(v)) }"
    )
    res = analyze(src)
    assert diag_codes(res) == ["OX0406"]
    assert drop_list(res) == []
    # The diagnostic carries a note pointing at the borrowing iterable.
    (diag,) = [d for d in res.diagnostics if d.code == "OX0406"]
    assert diag.notes


# ---------------------------------------------------------------------------
# Item 9 — new builtin modes (section 28, pinned)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("clone", ("read",)),
        ("get", ("read", "read")),
        ("range", ("read", "read")),
        ("print_str", ("read",)),
        ("str_len", ("read",)),
        ("concat", ("own", "own")),
        ("chars", ("read",)),
        ("int_to_str", ("read",)),
        ("parse_int", ("read",)),
    ],
)
def test_new_builtin_modes_pinned(name: str, expected: tuple[str, ...]) -> None:
    res = analyze("fn main() { print(0) }")
    assert diag_codes(res) == []
    assert param_modes(res, name) == expected


# ---------------------------------------------------------------------------
# Item 10 — never raises on new-construct garbage
# ---------------------------------------------------------------------------


GARBAGE_SOURCES = [
    "enum",
    "match x {",
    "for x in",
    "x =",
    "enum E { A(",
    "fn f() { x = }",
    "fn f() { match v { Some(x) => } }",
    "fn f() { for x in }",
]


@pytest.mark.parametrize("src", GARBAGE_SOURCES)
def test_never_raises_on_new_construct_garbage(src: str) -> None:
    res = analyze(src)
    assert res.diagnostics  # reported, never raised
    module, diags = parse_source(src)
    assert dump(module).startswith("(module")
    assert diags
