"""Blind golden tests for the Oxide parser — SPEC.md Part II, sections 6-13.

Every expectation below is derived from SPEC.md alone: grammar (section 6),
AST catalog (section 7), canonical dump format (section 8), Pratt binding
powers (section 10), error recovery (section 11), and the normative golden
dumps (section 12). One test function per section-13 plan item, parametrized
within items.
"""

import dataclasses

import pytest

from src.diagnostics import Span
from src.parser.ast import BinOp, Call, Let, Module, Var, dump
from src.parser.parser import parse_source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def d(src: str) -> str:
    """Canonical dump of the module parsed from *src*."""
    return dump(parse_source(src)[0])


def codes(src: str) -> list[str]:
    """All diagnostic codes for *src*: lexer codes first, then parser codes."""
    return [diag.code for diag in parse_source(src)[1]]


def mod_f(body_dump: str) -> str:
    """Expected module dump for a single `fn f()` whose block dumps as *body_dump*."""
    return f"(module (fn f (params) {body_dump}))"


def collect_node_ids(root: object) -> list[int]:
    """Gather every node_id in an AST by walking dataclasses.fields."""
    ids: list[int] = []
    stack: list[object] = [root]
    while stack:
        node = stack.pop()
        if dataclasses.is_dataclass(node) and hasattr(node, "node_id"):
            ids.append(node.node_id)
            for field in dataclasses.fields(node):
                stack.append(getattr(node, field.name))
        elif isinstance(node, tuple):
            stack.extend(node)
    return ids


# ---------------------------------------------------------------------------
# Golden sources and dumps (SPEC section 12, verbatim)
# ---------------------------------------------------------------------------

P1_SRC = "fn main() {\n    let x = 42\n    print(x)\n}\n"
P1_DUMP = "(module (fn main (params) (block (let (bind x) (lit int 42)) (tail (call (var print) (var x))))))"

P2_SRC = "fn f() { let y = 1 + 2 * 3 == 7 && !flag }"
P2_BODY = "(block (let (bind y) (bin && (bin == (bin + (lit int 1) (bin * (lit int 2) (lit int 3))) (lit int 7)) (un ! (var flag)))))"
P2_DUMP = f"(module (fn f (params) {P2_BODY}))"

P3_SRC = (
    "struct Point { x: Int, y: Int }\n"
    "\n"
    "fn add(p: Point) -> Int {\n"
    "    let Point { x, y } = p\n"
    "    x + y\n"
    "}\n"
)
P3_DUMP = "(module (struct Point (field x (type Int)) (field y (type Int))) (fn add (params (param p (type Point))) (ret (type Int)) (block (let (destruct Point x y) (var p)) (tail (bin + (var x) (var y))))))"

P4_SRC = (
    "fn f(a: Int) -> Int {\n"
    "    while a < 10 {\n"
    "        step()\n"
    "    }\n"
    "    if a > 0 {\n"
    "        a\n"
    "    } else if a == 0 {\n"
    "        make(Point { x: 1, y: 2 }).x\n"
    "    } else {\n"
    "        -a\n"
    "    }\n"
    "}\n"
)
P4_BODY = "(block (exprstmt (while (bin < (var a) (lit int 10)) (block (tail (call (var step)))))) (tail (if (bin > (var a) (lit int 0)) (block (tail (var a))) (if (bin == (var a) (lit int 0)) (block (tail (field (call (var make) (structlit Point (x (lit int 1)) (y (lit int 2)))) x))) (block (tail (un - (var a))))))))"
P4_DUMP = f"(module (fn f (params (param a (type Int))) (ret (type Int)) {P4_BODY}))"


# ---------------------------------------------------------------------------
# 1. Golden programs P1-P4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [(P1_SRC, P1_DUMP), (P2_SRC, P2_DUMP), (P3_SRC, P3_DUMP), (P4_SRC, P4_DUMP)],
    ids=["P1", "P2", "P3", "P4"],
)
def test_golden_programs_dump_exactly_with_zero_diagnostics(src: str, expected: str) -> None:
    # Act
    module, diagnostics = parse_source(src)
    # Assert
    assert dump(module) == expected
    assert diagnostics == []


# ---------------------------------------------------------------------------
# 2. Tail rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("fn f() { let x = 1 }", mod_f("(block (let (bind x) (lit int 1)))")),
        ("fn f() { 1 }", mod_f("(block (tail (lit int 1)))")),
        ("fn f() {\n    1\n}\n", mod_f("(block (tail (lit int 1)))")),
    ],
    ids=["let-has-no-tail", "single-line-tail", "newline-before-brace-still-tail"],
)
def test_tail_rule_last_expression_statement_becomes_block_tail(src: str, expected: str) -> None:
    assert d(src) == expected
    assert codes(src) == []


# ---------------------------------------------------------------------------
# 3. Params / args / fields: empty and trailing-comma forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("fn f() {}", mod_f("(block)")),
        ("fn f(a, b,) {}", "(module (fn f (params (param a) (param b)) (block)))"),
        ("fn f() { g() }", mod_f("(block (tail (call (var g))))")),
        ("fn f() { g(1, 2,) }", mod_f("(block (tail (call (var g) (lit int 1) (lit int 2))))")),
        ("struct S { a: Int, }", "(module (struct S (field a (type Int))))"),
        ("struct S {}", "(module (struct S))"),
        ("fn f() { let p = S { a: 1, } }", mod_f("(block (let (bind p) (structlit S (a (lit int 1)))))")),
    ],
    ids=[
        "empty-params",
        "trailing-comma-params",
        "empty-call-args",
        "trailing-comma-call-args",
        "trailing-comma-struct-decl",
        "empty-struct-decl",
        "trailing-comma-struct-lit",
    ],
)
def test_delimited_lists_support_empty_and_trailing_comma_forms(src: str, expected: str) -> None:
    assert d(src) == expected
    assert codes(src) == []


# ---------------------------------------------------------------------------
# 4. Types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        (
            "fn f(v: Vec<Vec<Int>>) {}",
            "(module (fn f (params (param v (type Vec (type Vec (type Int))))) (block)))",
        ),
        (
            "fn f(m: Map<Int, Str>) {}",
            "(module (fn f (params (param m (type Map (type Int) (type Str)))) (block)))",
        ),
        ("fn f() { let x: Int = 1 }", mod_f("(block (let (bind x) (type Int) (lit int 1)))")),
    ],
    ids=["nested-generic", "two-arg-generic", "let-annotation"],
)
def test_generic_types_and_let_annotations_dump(src: str, expected: str) -> None:
    assert d(src) == expected
    assert codes(src) == []


# ---------------------------------------------------------------------------
# 5. Precedence & associativity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "tail"),
    [
        ("a - b - c", "(bin - (bin - (var a) (var b)) (var c))"),
        ("a && b || c", "(bin || (bin && (var a) (var b)) (var c))"),
        ("-a * b", "(bin * (un - (var a)) (var b))"),
        ("-a.b", "(un - (field (var a) b))"),
        ("f(x)(y)", "(call (call (var f) (var x)) (var y))"),
        ("a.b.c", "(field (field (var a) b) c)"),
    ],
    ids=["sub-left", "and-binds-tighter-than-or", "neg-then-mul", "postfix-over-prefix", "curried-call", "field-chain"],
)
def test_precedence_and_associativity_shape_expression_trees(expr: str, tail: str) -> None:
    # Arrange
    src = f"fn f() {{ {expr} }}"
    # Act / Assert
    assert d(src) == mod_f(f"(block (tail {tail}))")
    assert codes(src) == []


# ---------------------------------------------------------------------------
# 6. Chained comparison
# ---------------------------------------------------------------------------


def test_chained_comparison_emits_exactly_one_ox0110_and_stays_left_assoc() -> None:
    # Arrange
    src = "fn f() { a < b < c }"
    # Act / Assert
    assert d(src) == mod_f("(block (tail (bin < (bin < (var a) (var b)) (var c))))")
    assert codes(src) == ["OX0110"]


# ---------------------------------------------------------------------------
# 7. If / else-if chains; if as an expression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        (
            "fn f() { if a { } else if b { } else { } }",
            mod_f("(block (tail (if (var a) (block) (if (var b) (block) (block)))))"),
        ),
        (
            "fn f() { let m = if c { 1 } else { 2 } }",
            mod_f("(block (let (bind m) (if (var c) (block (tail (lit int 1))) (block (tail (lit int 2))))))"),
        ),
    ],
    ids=["else-if-nests-as-if", "if-as-let-initializer"],
)
def test_else_if_chains_nest_as_if_in_else_slot(src: str, expected: str) -> None:
    assert d(src) == expected
    assert codes(src) == []


# ---------------------------------------------------------------------------
# 8. Struct-literal restriction in conditions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "fragment"),
    [
        ("fn f() { if x { } }", "(tail (if (var x) (block)))"),
        ("fn f() { while p { } }", "(while (var p) (block))"),
        ("fn f() { if (Point { x: 1 }) { } }", "(if (structlit Point (x (lit int 1))) (block))"),
    ],
    ids=["if-cond-is-var-not-structlit", "while-cond-is-var-not-structlit", "parenthesized-structlit-cond"],
)
def test_struct_literal_restriction_applies_in_conditions_but_lifts_in_parens(src: str, fragment: str) -> None:
    assert fragment in d(src)
    assert codes(src) == []


# ---------------------------------------------------------------------------
# 9. NEWLINE handling inside parens and after operators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        (
            "fn g() {\n    f(\n        x,\n        y\n    )\n}\n",
            "(module (fn g (params) (block (tail (call (var f) (var x) (var y))))))",
        ),
        (
            "fn g() {\n    let z = 1 +\n        2\n}\n",
            "(module (fn g (params) (block (let (bind z) (bin + (lit int 1) (lit int 2))))))",
        ),
    ],
    ids=["multiline-call-args", "operator-at-line-end-continues"],
)
def test_newlines_in_parens_and_trailing_operators_continue_expressions(src: str, expected: str) -> None:
    assert d(src) == expected
    assert codes(src) == []


# ---------------------------------------------------------------------------
# 10. return
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("fn f() { return }", mod_f("(block (return))")),
        ("fn f() { return x }", mod_f("(block (return (var x)))")),
    ],
    ids=["bare-return", "return-with-value"],
)
def test_return_parses_with_and_without_value(src: str, expected: str) -> None:
    assert d(src) == expected
    assert codes(src) == []


# ---------------------------------------------------------------------------
# 11. Recovery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected_codes", "fragments"),
    [
        (
            "fn f() { let = 5 }\nfn g() {}\n",
            ["OX0104"],
            ["(error)", "(fn g (params) (block))"],
        ),
        (
            "fn f() { (1 + ) }",
            ["OX0100"],
            ["(tail (bin + (lit int 1) (error)))"],
        ),
        (
            "42\nfn g() {}\n",
            ["OX0102"],
            ["(fn g (params) (block))"],
        ),
    ],
    ids=["bad-let-pattern-then-g-survives", "missing-operand-in-parens", "bad-top-level-item-then-g-survives"],
)
def test_recovery_yields_error_nodes_and_later_definitions_survive(
    src: str, expected_codes: list[str], fragments: list[str]
) -> None:
    # Act
    result = d(src)
    # Assert
    assert codes(src) == expected_codes
    for fragment in fragments:
        assert fragment in result


# ---------------------------------------------------------------------------
# 12. No cascade from lexer ERROR tokens
# ---------------------------------------------------------------------------


def test_lexer_error_token_does_not_cascade_into_parser_diagnostics() -> None:
    # Arrange
    src = "fn f() { let x = 123abc }"
    # Act
    cs = codes(src)
    # Assert
    assert "OX0004" in cs
    assert "OX0100" not in cs
    assert "(let (bind x) (error))" in d(src)


# ---------------------------------------------------------------------------
# 13. Diagnostic ordering: lexer before parser
# ---------------------------------------------------------------------------


def test_lexer_diagnostics_precede_parser_diagnostics() -> None:
    # Arrange: '@' is a lexer error (OX0001); 'let =' is a parser error (OX0104).
    src = "fn f() { let = @ }"
    # Act
    cs = codes(src)
    # Assert: lexer code first even though the parser error's span starts earlier.
    assert cs == ["OX0001", "OX0104"]


# ---------------------------------------------------------------------------
# 14. node_id uniqueness
# ---------------------------------------------------------------------------


def test_all_node_ids_in_one_parse_are_unique() -> None:
    # Act
    module, _ = parse_source(P4_SRC)
    ids = collect_node_ids(module)
    # Assert
    assert len(ids) > 1
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# 15. Spans
# ---------------------------------------------------------------------------


def test_let_and_binop_spans_cover_their_source_text_exactly() -> None:
    # Arrange
    src = "fn f() { let x = 1 + 2 }"
    # Act
    module, _ = parse_source(src)
    let_stmt = module.items[0].body.stmts[0]
    binop = let_stmt.init
    # Assert
    assert isinstance(let_stmt, Let)
    assert isinstance(binop, BinOp)
    assert (let_stmt.span.start, let_stmt.span.end) == (src.index("let"), src.index("2") + 1)
    assert (binop.span.start, binop.span.end) == (src.index("1"), src.index("2") + 1)


# ---------------------------------------------------------------------------
# 16. Never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    ['"\\u{', "/*/*/*", "\x00\xff@#$", "0x 0b2 9e", "fn", "fn f(", "{", "}}}", "fn f() -> {"],
)
def test_parse_source_never_raises_and_always_returns_a_module(src: str) -> None:
    # Act (must not raise on any input)
    module, diagnostics = parse_source(src)
    # Assert
    assert isinstance(module, Module)
    assert isinstance(diagnostics, list)


# ---------------------------------------------------------------------------
# Regressions: demonstrated defects
# ---------------------------------------------------------------------------


def test_trailing_while_statement_stays_exprstmt_not_tail() -> None:
    # A while_stmt is a distinct stmt production (SPEC section 6): the tail
    # rule converts only expression statements, so a block-final while must
    # dump as (exprstmt (while ...)), never as the block's tail.
    src = "fn f() { while c { } }"
    assert d(src) == mod_f("(block (exprstmt (while (var c) (block))))")
    assert codes(src) == []


def test_empty_destructure_pattern_is_rejected() -> None:
    # pattern := IDENT | IDENT "{" IDENT ("," IDENT)* [","] "}" requires at
    # least one field name inside the braces (SPEC section 6).
    src = "fn f() { let P { } = p }"
    assert "OX0101" in codes(src)
    assert "(destruct" not in d(src)


def test_newline_inside_destructure_pattern_braces_is_rejected() -> None:
    # Pattern braces are not one of the enumerated NEWLINE-skip contexts
    # (SPEC section 6), so a NEWLINE before the closing brace is an error.
    src = "fn f(p: P) {\n    let P { x,\n            y\n          } = p\n    x\n}\n"
    assert "OX0101" in codes(src)
    assert "(destruct" not in d(src)


def test_parenthesized_subexpression_spans_are_token_balanced() -> None:
    # Arrange
    src = "fn f() { (1 + 2) * 3 }"
    # Act
    module, diagnostics = parse_source(src)
    mul = module.items[0].body.tail
    # Assert: no span starts inside a paren pair it does not fully contain.
    assert diagnostics == []
    assert isinstance(mul, BinOp)
    assert src[mul.span.start : mul.span.end] == "(1 + 2) * 3"
    assert src[mul.lhs.span.start : mul.lhs.span.end] == "(1 + 2)"


@pytest.mark.parametrize(
    "src",
    ["fn f() { ( + ) }", "fn f() { + }"],
    ids=["empty-operand-parens", "bare-operator-statement"],
)
def test_broken_expression_region_emits_exactly_one_diagnostic(src: str) -> None:
    # SPEC section 11: one diagnostic per error region — a failed nud must
    # not cascade into extra OX0100s via the binary-operator loop.
    assert codes(src) == ["OX0100"]


def test_newline_before_lbrace_or_else_is_tolerated() -> None:
    # SPEC Part VII section 34 (supersedes the earlier ruling): a NEWLINE run
    # is skipped between a fn/if/while/for/match header and its '{', and
    # between '}' and 'else' (golden W6).
    module, diagnostics = parse_source("fn f()\n{\n    1\n}\n")
    assert diagnostics == []
    assert dump(module) == "(module (fn f (params) (block (tail (lit int 1)))))"
    else_src = "fn f() {\n    if c {\n        1\n    }\n    else {\n        2\n    }\n}\n"
    else_module, else_diagnostics = parse_source(else_src)
    assert else_diagnostics == []
    assert dump(else_module) == (
        "(module (fn f (params) (block (tail (if (var c) "
        "(block (tail (lit int 1))) (block (tail (lit int 2))))))))"
    )


@pytest.mark.parametrize(
    ("label", "src"),
    [
        ("parens", "fn f() { " + "(" * 400 + "1" + ")" * 400 + " }"),
        ("ifs", "fn f() { " + "if a { " * 250 + "x" + " }" * 250 + " }"),
        ("bangs", "fn f() { " + "!" * 600 + "x }"),
        ("whiles", "fn f() { " + "while a { " * 400 + "}" * 400 + " }"),
        ("types", "fn f(v: " + "Vec<" * 1200 + "Int" + ">" * 1200 + ") {}"),
        ("else-ifs", "fn f() { if a { } " + "else if a { } " * 1200 + "}"),
    ],
    ids=["parens", "ifs", "bangs", "whiles", "types", "else-ifs"],
)
def test_parse_source_never_raises_on_pathologically_deep_nesting(
    label: str, src: str
) -> None:
    # Act (SPEC section 9: the parser NEVER raises — deep nesting included)
    module, diagnostics = parse_source(src)
    # Assert
    assert isinstance(module, Module)
    assert isinstance(diagnostics, list)


def test_dump_renders_very_long_operator_and_field_chains() -> None:
    # Arrange: valid diagnostics-free programs whose ASTs nest thousands deep.
    binop_src = "fn f() { 1" + " + 1" * 2000 + " }"
    field_src = "fn f() { a" + ".b" * 3000 + " }"
    # Act
    binop_module, binop_diags = parse_source(binop_src)
    field_module, field_diags = parse_source(field_src)
    # Assert: dump must not overflow the call stack on ASTs the parser builds.
    assert binop_diags == []
    assert dump(binop_module).count("(bin +") == 2000
    assert field_diags == []
    assert dump(field_module).count("(field") == 3000


# ---------------------------------------------------------------------------
# §53 builtin method syntax: `recv.name(args)` == `name(recv, args)`
# ---------------------------------------------------------------------------


class TestBuiltinMethodSyntax:
    """Sugar added because 82% of failing Oxide repairs on the ownership
    probe contained `.clone()` -- the single largest failure mode, and the
    only Rust idiom the language card failed to suppress (`let mut`, `;`,
    `vec![]` and indexing appeared zero times in 120 failures).
    """

    def test_parser_and_sema_builtin_sets_stay_in_sync(self):
        """The parser mirrors the builtin names rather than importing sema,
        which would invert the layering. This is the guard against drift:
        adding a builtin to sema without adding it here would silently
        leave it un-callable as a method."""
        from src.parser.expressions import BUILTIN_METHOD_NAMES
        from src.sema.types import BUILTINS

        assert BUILTIN_METHOD_NAMES == set(BUILTINS), (
            "parser/sema builtin sets diverged: "
            f"sema-only={sorted(set(BUILTINS) - BUILTIN_METHOD_NAMES)}, "
            f"parser-only={sorted(BUILTIN_METHOD_NAMES - set(BUILTINS))}"
        )

    def test_method_call_desugars_to_a_plain_call(self):
        """It must produce an ordinary Call, not a call on a FieldAccess --
        that is what lets resolution, use-context classification, linearity
        and codegen all behave exactly as for the prefix form."""
        assert d("fn main() { let w = v.clone() }") == d(
            "fn main() { let w = clone(v) }"
        )

    def test_extra_arguments_follow_the_receiver(self):
        assert d("fn main() { let w = v.push(1) }") == d(
            "fn main() { let w = push(v, 1) }"
        )

    def test_method_calls_chain(self):
        assert d("fn main() { let v = vec().push(1).push(2) }") == d(
            "fn main() { let v = push(push(vec(), 1), 2) }"
        )

    def test_field_access_without_a_call_is_untouched(self):
        """`p.clone` with no parentheses stays a field access -- only the
        call form is sugar."""
        assert "(field " in d("fn main() { let x = p.clone }")

    def test_non_builtin_name_stays_a_field_access(self):
        """`p.area()` is not sugar: Oxide has no user-defined methods and
        no callable fields, so this remains what it always was."""
        assert "(field " in d("fn main() { let x = p.area() }")


# ---------------------------------------------------------------------------
# §55 `vec(...)` list literal: `vec(a, b, c)` == `push(push(push(vec(), a), b), c)`
# ---------------------------------------------------------------------------


class TestVecLiteralSugar:
    """Sugar added because models call `vec(3, 8, -2, 12, 7)` as a variadic
    list constructor; Oxide's `vec()` is 0-arity. Dominant OX0303 sub-class
    across all three model families in the v0.3 taxonomy, arity always
    matching the task's list length -- the intent is unambiguous.
    """

    def test_zero_arg_vec_is_unchanged(self):
        """`vec()` stays a bare 0-arg call -- the desugar loop is a no-op."""
        assert d("fn main() { let v = vec() }") == (
            "(module (fn main (params) (block (let (bind v) (call (var vec))))))"
        )

    def test_one_arg_vec_desugars_to_a_single_push(self):
        assert d("fn main() { let v = vec(1) }") == d(
            "fn main() { let v = push(vec(), 1) }"
        )

    def test_three_arg_vec_desugars_to_a_push_chain(self):
        assert d("fn main() { let v = vec(3, 8, -2) }") == d(
            "fn main() { let v = push(push(push(vec(), 3), 8), -2) }"
        )

    def test_nested_vec_literals_desugar_independently(self):
        assert d("fn main() { let v = vec(vec(1), vec(2)) }") == d(
            "fn main() { let v = push(push(vec(), push(vec(), 1)), push(vec(), 2)) }"
        )

    def test_vec_literal_desugars_in_expression_position(self):
        assert d("fn main() { print(len(vec(1, 2, 3))) }") == d(
            "fn main() { print(len(push(push(push(vec(), 1), 2), 3))) }"
        )

    def test_desugared_call_is_an_ordinary_call_not_a_field_access(self):
        """The synthesized `push` callees must be plain Var nodes -- the
        same shape `_builtin_method` produces for `.push(...)` -- so no
        later stage can tell sugar was involved."""
        dump = d("fn main() { let v = vec(1) }")
        assert "(field " not in dump
        assert dump.count("(call (var push)") == 1

    def test_synthesized_nodes_carry_the_original_calls_span_not_the_args(self):
        """SPEC.md §55's normative span claim, checked directly on the raw
        AST rather than inferred from where a diagnostic happens to land
        (a diagnostic on an argument would pass this check even if every
        synthesized node carried `Span(0, 0)`): every synthesized `push`
        Call/Var in the desugared chain carries the ORIGINAL `vec(1, 2)`
        call's span; the innermost `vec()` call reuses the real `vec`
        token's own (tighter) span for its callee; and the argument
        literals keep their own real spans, untouched."""
        src = "fn f() { let v = vec(1, 2) }"
        vec_start = src.index("vec(")
        vec_end = vec_start + len("vec")
        call_end = src.index(")", vec_start) + 1
        call_span = Span(vec_start, call_end)

        module, diagnostics = parse_source(src)
        assert diagnostics == []
        let_stmt = module.items[0].body.stmts[0]

        outer_push = let_stmt.init
        assert isinstance(outer_push, Call)
        assert isinstance(outer_push.callee, Var)
        assert outer_push.callee.name == "push"
        inner_push, lit2 = outer_push.args
        assert isinstance(inner_push, Call)
        assert isinstance(inner_push.callee, Var)
        assert inner_push.callee.name == "push"
        vec_call, lit1 = inner_push.args
        assert isinstance(vec_call, Call)
        assert isinstance(vec_call.callee, Var)
        assert vec_call.callee.name == "vec"
        assert vec_call.args == ()

        # Synthesized nodes (no real source token of their own) carry the
        # ORIGINAL call's span -- not a fabricated Span(0, 0) or a
        # narrower/misleading guess.
        assert outer_push.span == call_span
        assert outer_push.callee.span == call_span
        assert inner_push.span == call_span
        assert inner_push.callee.span == call_span
        assert vec_call.span == call_span

        # The innermost call's callee reuses the REAL, already-parsed
        # `vec` token -- its own tight span, not the full call's.
        assert vec_call.callee.span == Span(vec_start, vec_end)
        assert vec_call.callee.span != call_span

        # Argument expressions keep their own real (narrower) spans --
        # never widened to the synthesized call's span.
        assert lit1.span != call_span
        assert lit2.span != call_span
        assert call_span.start <= lit1.span.start <= lit1.span.end <= call_span.end
        assert call_span.start <= lit2.span.start <= lit2.span.end <= call_span.end

    def test_receiver_form_vec_is_not_variadic_sugar(self):
        """`x.vec(...)` goes through §53's method desugar (`vec` is in
        BUILTIN_METHOD_NAMES only because it mirrors sema's builtin set) to
        a flat `vec(x, 1)` Call -- it is untouched by §55, which only fires
        on the plain-call spelling `vec(...)` seen directly by `_postfix`,
        not on a Call `_builtin_method` already built."""
        assert d("fn main() { let v = x.vec(1) }") == (
            "(module (fn main (params) (block (let (bind v) "
            "(call (var vec) (var x) (lit int 1))))))"
        )


# ---------------------------------------------------------------------------
# §56 field assignment: `s.f = e` at arbitrary field depth
# ---------------------------------------------------------------------------


class TestFieldAssignment:
    """Models assign struct fields in place and the language had no such
    form. Under constrained decoding the want deformed into a discarded
    `==` comparison (18 occurrences in 9 of 600 constrained first attempts,
    0 of 600 unconstrained) -- the §54 lesson, third instance.
    """

    def test_single_field_assignment_parses(self):
        assert d("fn f() { p.x = 5 }") == mod_f(
            "(block (field-assign p.x (lit int 5)))"
        )

    def test_nested_path_parses(self):
        assert d("fn f() { a.b.c = 5 }") == mod_f(
            "(block (field-assign a.b.c (lit int 5)))"
        )

    def test_double_equals_is_still_a_comparison(self):
        """EQEQ is a distinct token kind, so the scan never fires here."""
        assert "field-assign" not in d("fn f() { p.x == 5 }")

    def test_plain_field_access_statement_is_unchanged(self):
        """A failed scan restores the cursor: `p.x` alone stays an expr."""
        assert "field-assign" not in d("fn f() { p.x }")

    def test_scan_does_not_see_through_a_newline(self):
        """`p.x` at end of line is an expression statement, never the start
        of an assignment -- the rule _peek_next documents for §26."""
        assert "field-assign" not in d("fn f() { p.x\n y = 5 }")

    def test_whole_variable_assignment_is_untouched(self):
        assert d("fn f() { p = 5 }") == mod_f("(block (assign p (lit int 5)))")
