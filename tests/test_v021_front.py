"""Blind tests for v0.2.1 front end — SPEC.md Part VII, section 38 first file.

Covers the full section-38 plan: lexer surface for break/continue/? (§34),
dump forms for break/continue/try/rest and `?` precedence (§35/§38), OX0105
loop scoping, the section-36 goldens W1-W7 front-end halves plus ALL pinned
negatives, before-jump drops, OX0403 with a continue skipping the
reassignment, OX0406 unchanged, newline tolerance, and garbage inputs.
"""

from __future__ import annotations

import pytest

from src.lexer.lexer import Lexer
from src.lexer.tokens import TERMINATOR_SET, TokenKind
from src.parser.ast import dump
from src.parser.parser import parse_source
from src.sema.analyze import (analyze, diag_codes, drop_list, param_modes,
                              use_classes, var_types_by_name)

K = TokenKind

# --- Helpers ---


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


# --- Lexer surface (section 34) ---


def test_break_and_continue_lex_as_terminator_keywords() -> None:
    assert kinds("break") == [K.KW_BREAK, K.NEWLINE, K.EOF]
    assert kinds("continue") == [K.KW_CONTINUE, K.NEWLINE, K.EOF]
    assert K.KW_BREAK in TERMINATOR_SET
    assert K.KW_CONTINUE in TERMINATOR_SET
    # Maximal munch: keyword-prefixed identifiers stay IDENT.
    assert kinds("breaker continued") == [K.IDENT, K.IDENT, K.NEWLINE, K.EOF]


def test_question_token_lexes_clean() -> None:
    lexer = Lexer("let x = f(y)?")
    toks = lexer.tokenize()
    assert lexer.diagnostics == []  # no OX0001 any more
    assert not any(t.kind == K.ERROR for t in toks)
    question = [t for t in toks if t.kind == K.QUESTION]
    assert len(question) == 1
    assert question[0].lexeme == "?"
    assert toks[-1].kind == K.EOF
    midline = [K.IDENT, K.QUESTION, K.PLUS, K.INT, K.NEWLINE, K.EOF]
    assert kinds("x? + 1") == midline


# --- Dump forms (section 35) and `?` precedence (section 38 pinned) ---


@pytest.mark.parametrize("jump", ["break", "continue"])
def test_break_and_continue_statement_dumps(jump: str) -> None:
    assert d_clean("fn f() { while true { " + jump + " } }") == (
        "(module (fn f (params) (block (exprstmt "
        f"(while (lit bool true) (block ({jump})))))))"
    )


def test_try_precedence_pinned_dump() -> None:
    assert d_clean("fn f() { get(v, 0)? + 1 }") == (
        "(module (fn f (params) (block (tail (bin + "
        "(try (call (var get) (var v) (lit int 0))) (lit int 1))))))"
    )


@pytest.mark.parametrize(
    ("body", "tail"),
    [
        pytest.param("-a?", "(un - (try (var a)))", id="prefix-vs-postfix"),
        pytest.param("a.b?", "(try (field (var a) b))", id="try-of-field"),
        pytest.param("a?.b", "(field (try (var a)) b)", id="field-of-try"),
        pytest.param("f(x)?", "(try (call (var f) (var x)))", id="try-of-call"),
        pytest.param("x??", "(try (try (var x)))", id="double-try"),
    ],
)
def test_try_postfix_tier_dumps(body: str, tail: str) -> None:
    src = "fn f() { " + body + " }"
    assert d_clean(src) == f"(module (fn f (params) (block (tail {tail}))))"


@pytest.mark.parametrize(
    ("body", "lit_part"),
    [
        pytest.param("Point { x: 5, ..p }", "(x (lit int 5)) ", id="subset"),
        pytest.param("Point { ..p }", "", id="rest-only"),
        pytest.param("Point { x: 5, ..p, }", "(x (lit int 5)) ", id="trailing-comma"),
    ],
)
def test_struct_update_rest_dumps(body: str, lit_part: str) -> None:
    src = "fn f(p: Point) { " + body + " }"
    assert d_clean(src) == (
        "(module (fn f (params (param p (type Point))) (block (tail "
        f"(structlit Point {lit_part}(rest (var p)))))))"
    )


# --- OX0105 — break/continue outside a loop (section 34) ---


def test_break_outside_loop_pinned_negative() -> None:
    module, diags = parse_source("fn f() { break }")
    assert [dg.code for dg in diags] == ["OX0105"]  # a parser diagnostic
    assert dump(module) == "(module (fn f (params) (block (break))))"
    # Parse diagnostics gate sema entirely (section 15 gates).
    assert codes_of("fn f() { break }") == ["OX0105"]


NESTED_THEN_OUTSIDE = (
    "fn f(c: Bool) { while c { while c { break } } }\n"
    "fn g(c: Bool) { break }"
)
AFTER_LOOP_CLOSES = "fn f(c: Bool) { while c { while c { break } }\n break }"


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("fn f() { continue }", id="continue-outside"),
        pytest.param("fn f(c: Bool) { if c { break } }", id="if-no-loop"),
        pytest.param(AFTER_LOOP_CLOSES, id="after-loop-closes"),
        pytest.param(NESTED_THEN_OUTSIDE, id="fn-boundary-resets-depth"),
    ],
)
def test_jump_outside_loop_reports_ox0105(src: str) -> None:
    assert codes_of(src) == ["OX0105"]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("fn f(c: Bool) { while c { break } }", id="break-in-while"),
        pytest.param("fn f(c: Bool) { while c { continue } }", id="continue-in-while"),
        pytest.param("fn f(v: Vec<Int>) { for x in v { break } }", id="break-in-for"),
    ],
)
def test_break_and_continue_legal_inside_loops(src: str) -> None:
    assert codes_of(src) == []


# --- Goldens W1-W7, front-end halves (section 36) ---

W1_SRC = (
    "fn first_big(v: Vec<Int>) -> Int { let found = -1\n"
    " for x in v { if x > 10 { found = x\n break } }\n"
    " found }\n"
    "fn main() { print(first_big(push(push(push(vec(), 3), 42), 99))) }"
)


def test_w1_break_out_of_for_front_half() -> None:
    res = analyze(W1_SRC)
    assert diag_codes(res) == []
    assert param_modes(res, "first_big") == ("read",)
    assert var_types_by_name(res, "first_big", "found") == ["Int"]
    assert drop_list(res) == []


W2_SRC = (
    "fn second(v: Vec<Int>) -> Option<Int> { let x = get(v, 1)?\n"
    " Some(x + 1) }\n"
    "fn main() { match second(push(push(vec(), 5), 6)) {"
    " Some(x) => print(x), None => print(0), } }"
)


def test_w2_try_propagation_front_half() -> None:
    res = analyze(W2_SRC)
    assert diag_codes(res) == []
    assert var_types_by_name(res, "second", "v") == ["Vec<Int>"]
    # `?` on Option<Int> yields the payload type.
    assert var_types_by_name(res, "second", "x") == ["Int"]
    assert param_modes(res, "second") == ("read",)
    assert drop_list(res) == []


W3_SRC = (
    "struct Bag { items: Vec<Int> }\n"
    "fn main() { let b = Bag { items: push(vec(), 4) }\n"
    " let c = b.items\n"
    " print(len(c))\n"
    " print(len(b.items)) }"
)


def test_w3_field_access_is_implicit_clone_front_half() -> None:
    res = analyze(W3_SRC)
    # OX0405 is retired: non-copy field access is legal everywhere.
    assert diag_codes(res) == []
    # The base of a field access stays a read use (section 36).
    assert use_classes(res, "main", "b") == ["read", "read"]
    assert var_types_by_name(res, "main", "c") == ["Vec<Int>"]
    assert drop_list(res) == [
        ("main", "b", "after-stmt"),
        ("main", "c", "after-stmt"),
    ]


W4_SRC = (
    "struct Point { x: Int, y: Int }\n"
    "fn main() { let p = Point { x: 1, y: 2 }\n"
    " let q = Point { x: 5, ..p }\n"
    " let Point { x, y } = q\n"
    " print(x + y) }"
)


def test_w4_functional_update_front_half() -> None:
    res = analyze(W4_SRC)
    assert diag_codes(res) == []
    # ..rest consumes; destructure consumes; every value moves exactly once.
    assert use_classes(res, "main", "p") == ["move"]
    assert use_classes(res, "main", "q") == ["move"]
    assert var_types_by_name(res, "main", "q") == ["Point"]
    assert drop_list(res) == []


W5_SRC = (
    "fn sum_odds(n: Int) -> Int { let s = 0\n"
    " for i in range(0, n) { if i % 2 == 0 { continue }\n"
    " s = s + i }\n"
    " s }\n"
    "fn main() { print(sum_odds(6)) }"
)


def test_w5_continue_front_half() -> None:
    res = analyze(W5_SRC)
    assert diag_codes(res) == []
    assert param_modes(res, "sum_odds") == ("read",)
    assert drop_list(res) == []


def test_w6_newline_before_fn_brace() -> None:
    src = "fn f()\n{\n    1\n}"
    assert d_clean(src) == "(module (fn f (params) (block (tail (lit int 1)))))"
    assert codes_of(src) == []


def test_w6_else_on_own_line() -> None:
    src = "fn g(c: Bool) -> Int {\n    if c {\n        1\n    }\n    else {\n        2\n    }\n}"
    assert d_clean(src) == (
        "(module (fn g (params (param c (type Bool))) (ret (type Int)) "
        "(block (tail (if (var c) (block (tail (lit int 1))) "
        "(block (tail (lit int 2))))))))"
    )
    assert codes_of(src) == []
    # else-if chain with every else on its own line.
    src = (
        "fn g(c: Bool, d: Bool) -> Int {\n    if c {\n        1\n    }\n"
        "    else if d {\n        2\n    }\n    else {\n        3\n    }\n}"
    )
    assert d_clean(src) == (
        "(module (fn g (params (param c (type Bool)) (param d (type Bool))) "
        "(ret (type Int)) (block (tail (if (var c) "
        "(block (tail (lit int 1))) (if (var d) (block (tail (lit int 2))) "
        "(block (tail (lit int 3)))))))))"
    )
    assert codes_of(src) == []


def test_w7_to_float_trunc_front_half() -> None:
    src = "fn main() { print(trunc(to_float(7) / 2.0)) }"
    res = analyze(src)
    assert diag_codes(res) == []
    assert param_modes(res, "to_float") == ("read",)
    assert param_modes(res, "trunc") == ("read",)
    assert drop_list(res) == []


# --- Newline tolerance beyond W6 (section 34): header NEWLINE runs before `{` ---


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        pytest.param(
            "fn f(c: Bool) {\n    if c\n    {\n        print(1)\n    }\n}",
            "(module (fn f (params (param c (type Bool))) (block (tail "
            "(if (var c) (block (tail (call (var print) (lit int 1)))))))))",
            id="if-header",
        ),
        pytest.param(
            "fn f(c: Bool) {\n    while c\n    {\n        print(1)\n    }\n}",
            "(module (fn f (params (param c (type Bool))) (block (exprstmt "
            "(while (var c) (block (tail (call (var print) (lit int 1)))))))))",
            id="while-header",
        ),
        pytest.param(
            "fn f(v: Vec<Int>) {\n    for x in v\n    {\n        print(x)\n    }\n}",
            "(module (fn f (params (param v (type Vec (type Int)))) "
            "(block (exprstmt (for x (var v) "
            "(block (tail (call (var print) (var x)))))))))",
            id="for-header",
        ),
        pytest.param(
            "fn f(o: Option<Int>) -> Int {\n    match o\n    {\n"
            "        Some(x) => x,\n        None => 0,\n    }\n}",
            "(module (fn f (params (param o (type Option (type Int)))) "
            "(ret (type Int)) (block (tail (match (var o) "
            "(arm (vpat Some x) (var x)) (arm (vpat None) (lit int 0)))))))",
            id="match-header",
        ),
    ],
)
def test_newline_run_skipped_between_header_and_brace(src: str, expected: str) -> None:
    assert d_clean(src) == expected
    assert codes_of(src) == []


# --- `?` and functional-update semantics (section 36) — pinned negatives et al. ---

POINT_DECL = "struct Point { x: Int, y: Int }\n"


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        pytest.param(
            "fn f(v: Vec<Int>) -> Int { let x = get(v, 0)?\n x }",
            ["OX0308"],
            id="try-in-int-fn",
        ),
        pytest.param("fn f() { 1? }", ["OX0308"], id="try-on-int-operand"),
        pytest.param(
            "fn f(v: Vec<Int>) -> Result<Int, Str> { let x = get(v, 0)?\n Ok(x) }",
            ["OX0308"],
            id="option-try-in-result-fn",
        ),
        pytest.param(
            POINT_DECL + "fn f(p: Point) -> Point { Point { z: 1, ..p } }",
            ["OX0304"],
            id="update-unknown-field",
        ),
        pytest.param(
            POINT_DECL + "fn f(p: Point) -> Point { Point { x: 1, x: 2, ..p } }",
            ["OX0304"],
            id="update-duplicate-field",
        ),
        pytest.param(
            "struct S1 { a: Int }\nstruct S2 { a: Int }\n"
            "fn f(p: S2) -> S1 { S1 { ..p } }",
            ["OX0300"],
            id="update-wrong-struct",
        ),
        pytest.param(
            POINT_DECL + "fn f(p: Point) -> Point { Point { ..p } }",
            [],
            id="update-rest-only-legal",
        ),
    ],
)
def test_try_and_update_code_matrix(src: str, expected: list[str]) -> None:
    assert codes_of(src) == expected


def test_try_on_result_propagates_and_moves_param() -> None:
    src = "fn f(r: Result<Int, Str>) -> Result<Int, Str> { let x = r?\n Ok(x + 1) }"
    res = analyze(src)
    assert diag_codes(res) == []
    assert var_types_by_name(res, "f", "x") == ["Int"]
    # The `?` operand is a MOVE use, so the param mode is own.
    assert param_modes(res, "f") == ("own",)


# --- before-jump drops and multi-exit merges (section 36) ---


@pytest.mark.parametrize("jump", ["break", "continue"])
def test_before_jump_drop_for_loop_local_live_at_jump(jump: str) -> None:
    src = (
        "fn f(c: Bool) { while c { let w = push(vec(), 1)\n"
        " if c { " + jump + " }\n"
        " print(len(w)) } }"
    )
    res = analyze(src)
    assert diag_codes(res) == []
    assert drop_list(res) == [
        ("f", "w", "after-stmt"),
        ("f", "w", "before-jump"),
    ]


def test_inner_loop_break_does_not_drop_outer_loop_locals() -> None:
    src = (
        "fn f(c: Bool) { while c { let w = push(vec(), 1)\n"
        " while c { break }\n"
        " print(len(w)) } }"
    )
    res = analyze(src)
    assert diag_codes(res) == []
    # The inner break scopes to the inner loop only: w survives it.
    assert drop_list(res) == [("f", "w", "after-stmt")]


def test_break_edge_move_live_after_reports_ox0400() -> None:
    src = (
        "fn f(c: Bool) { let v = push(vec(), 1)\n"
        " while c { if c { let w = push(v, 2)\n"
        " print(len(w))\n"
        " break } }\n"
        " print(len(v)) }"
    )
    res = analyze(src)
    assert diag_codes(res) == ["OX0400"]
    (diag,) = [dg for dg in res.diagnostics if dg.code == "OX0400"]
    assert diag.notes
    assert drop_list(res) == []  # error fn contributes no drops


def test_break_edge_move_dead_after_hoists_drop() -> None:
    src = (
        "fn g(c: Bool) { let v = push(vec(), 1)\n"
        " while c { if c { let w = push(v, 2)\n"
        " print(len(w))\n"
        " break } } }"
    )
    res = analyze(src)
    assert diag_codes(res) == []
    dl = drop_list(res)
    assert ("g", "w", "after-stmt") in dl
    # v is dead after the loop: the still-owned (non-break) edge drops it.
    v_entries = [t for t in dl if t[0] == "g" and t[1] == "v"]
    assert len(v_entries) == 1
    assert len(dl) == 2


def test_continue_skipping_reassignment_reports_ox0403() -> None:
    src = (
        "fn f(c: Bool) { let acc = push(vec(), 1)\n"
        " while c { let tmp = push(acc, 2)\n"
        " if c { continue }\n"
        " acc = tmp } }"
    )
    res = analyze(src)
    assert diag_codes(res) == ["OX0403"]
    (diag,) = [dg for dg in res.diagnostics if dg.code == "OX0403"]
    assert diag.notes
    assert drop_list(res) == []
    # Control: the same loop without the continue re-owns before the
    # back edge and is clean (section 28 accumulation idiom).
    assert codes_of(src.replace(" if c { continue }\n", "")) == []


def test_ox0406_unchanged() -> None:
    src = (
        "fn main() { let v = push(vec(), 1)\n"
        " for x in v { v = push(vec(), x) }\n"
        " print(len(v)) }"
    )
    res = analyze(src)
    assert diag_codes(res) == ["OX0406"]
    assert drop_list(res) == []


# --- Never raises on v0.2.1-construct garbage (section 38) ---

GARBAGE_SOURCES = [
    "break",
    "continue",
    "?",
    "..",
    "x?",
    "..p",
    "break }",
    "fn f() { ? }",
    "fn f() { .. }",
    "fn f() { break",
    "fn f() { Point { ..p",
    "match x\n{",
]


@pytest.mark.parametrize("src", GARBAGE_SOURCES)
def test_never_raises_on_v021_garbage(src: str) -> None:
    res = analyze(src)
    assert res.diagnostics  # reported, never raised
    module, diags = parse_source(src)
    assert dump(module).startswith("(module")
    assert diags
