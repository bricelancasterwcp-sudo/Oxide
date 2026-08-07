"""Lexer conformance suite for Oxide Phase 1.

Every test maps to a numbered item of SPEC.md §5 and asserts the golden token
sequences of SPEC.md §4 exactly. Authored from the spec alone (strict TDD).
"""

from __future__ import annotations

import pytest

from src.lexer.lexer import Lexer
from src.lexer.tokens import TokenKind

K = TokenKind


def toks(src: str) -> list:
    """All tokens for ``src`` (always terminated by exactly one EOF)."""
    return Lexer(src).tokenize()


def kinds(src: str) -> list[TokenKind]:
    """The token kind sequence for ``src``."""
    return [t.kind for t in Lexer(src).tokenize()]


def diags(src: str) -> list:
    """Diagnostics queued while lexing ``src``, in source order."""
    lexer = Lexer(src)
    lexer.tokenize()
    return list(lexer.diagnostics)


# §5.1 — golden examples G1, G2, G3 (SPEC §4)

G1_SRC = "fn main() {\n" "    let x = 42\n" "    print(x)\n" "}\n"
G2_SRC = "let y = 1.5 * (2 + x) >= 0x1F && a != b"
G3_SRC = "Vec::new()"


def test_g1_canonical_program_matches_golden_sequence_and_int_value():
    # Act
    tokens = toks(G1_SRC)

    # Assert — no NEWLINE after `{`; LBRACE is not a terminator.
    assert [t.kind for t in tokens] == [
        K.KW_FN, K.IDENT, K.LPAREN, K.RPAREN, K.LBRACE,
        K.KW_LET, K.IDENT, K.EQ, K.INT, K.NEWLINE,
        K.IDENT, K.LPAREN, K.IDENT, K.RPAREN, K.NEWLINE,
        K.RBRACE, K.NEWLINE, K.EOF]
    assert [t.lexeme for t in tokens if t.kind is K.IDENT] == ["main", "x", "print", "x"]
    assert [t.value for t in tokens if t.kind is K.INT] == [42]
    assert diags(G1_SRC) == []


def test_g2_operators_and_literals_match_golden_sequence_and_values():
    # Act
    tokens = toks(G2_SRC)

    # Assert — trailing NEWLINE comes from EOF injection (prev IDENT).
    assert [t.kind for t in tokens] == [
        K.KW_LET, K.IDENT, K.EQ, K.FLOAT, K.STAR, K.LPAREN, K.INT, K.PLUS,
        K.IDENT, K.RPAREN, K.GEQ, K.INT, K.ANDAND, K.IDENT, K.NEQ, K.IDENT,
        K.NEWLINE, K.EOF]
    assert [t.value for t in tokens if t.kind is K.FLOAT] == [1.5]
    assert [t.value for t in tokens if t.kind is K.INT] == [2, 31]
    assert diags(G2_SRC) == []


def test_g3_path_separator_matches_golden_sequence():
    # Act / Assert
    assert kinds(G3_SRC) == [
        K.IDENT, K.PATH_SEP, K.IDENT, K.LPAREN, K.RPAREN, K.NEWLINE, K.EOF]
    assert [t.lexeme for t in toks(G3_SRC) if t.kind is K.IDENT] == ["Vec", "new"]
    assert diags(G3_SRC) == []


# §5.2 — Go-style implicit statement termination (SPEC §3.2)


def test_no_newline_after_lbrace_or_trailing_binary_operator():
    # Act / Assert — `{` then newline emits nothing.
    assert kinds("{\n}\n") == [K.LBRACE, K.RBRACE, K.NEWLINE, K.EOF]
    # A line ending in a binary operator continues onto the next line.
    assert kinds("x = 1 +\n2\n") == [
        K.IDENT, K.EQ, K.INT, K.PLUS, K.INT, K.NEWLINE, K.EOF]
    assert kinds("x =\n1\n") == [K.IDENT, K.EQ, K.INT, K.NEWLINE, K.EOF]


def test_blank_line_runs_collapse_to_a_single_newline():
    # Act / Assert — NEWLINE is not itself a terminator kind.
    assert kinds("x\n\n\n\ny\n") == [K.IDENT, K.NEWLINE, K.IDENT, K.NEWLINE, K.EOF]
    assert kinds("x\n   \n\t\ny\n") == [K.IDENT, K.NEWLINE, K.IDENT, K.NEWLINE, K.EOF]


@pytest.mark.parametrize(
    ("src", "expect_newline"),
    [
        # terminator kinds: IDENT INT FLOAT STRING KW_TRUE KW_FALSE KW_RETURN
        # RPAREN RBRACE
        ("x", True), ("42", True), ("4.2", True), ('"s"', True),
        ("true", True), ("false", True), ("return", True),
        ("f()", True), ("{}", True),
        # everything else, including ERROR
        ("let", False), ("fn", False), ("if", False), ("else", False),
        ("while", False), ("struct", False), ("match", False), ("x +", False),
        ("x =", False), ("(", False), ("{", False), ("@", False),
        # v0.2 keywords (§26): TERMINATOR_SET unchanged
        ("for", False), ("in", False), ("enum", False),
        # v0.2.1 (§34): break/continue terminate like KW_RETURN; postfix `?`
        # ends an expression like RPAREN (required by the §36 W2 golden).
        ("break", True), ("continue", True), ("x?", True)],
)
def test_newline_injected_before_eof_only_after_terminator_kinds(src, expect_newline):
    # Act
    tokens = toks(src)

    # Assert
    assert tokens[-1].kind is K.EOF
    assert (tokens[-2].kind is K.NEWLINE) is expect_newline


# §5.3, §5.4, §5.5 — comments (SPEC §3.1)


def test_line_comment_does_not_suppress_newline_after_int():
    # Arrange
    src = "x = 1 // trailing comment\n"

    # Act / Assert — comments never update prev_kind.
    assert kinds(src) == [K.IDENT, K.EQ, K.INT, K.NEWLINE, K.EOF]
    assert kinds("x // c") == [K.IDENT, K.NEWLINE, K.EOF]
    assert diags(src) == []


def test_nested_block_comment_is_skipped_entirely():
    # Arrange
    src = "let a = /* a /* b */ c */ 1\n"

    # Act / Assert — the inner `*/` must not close the outer comment.
    assert kinds(src) == [K.KW_LET, K.IDENT, K.EQ, K.INT, K.NEWLINE, K.EOF]
    assert diags(src) == []
    # A block comment never triggers NEWLINE emission, even spanning lines.
    assert kinds("x /* multi\nline */ y") == [K.IDENT, K.IDENT, K.NEWLINE, K.EOF]


def test_unterminated_block_comment_reports_ox0002_with_error_token():
    # Arrange
    src = "let x = /* oops"

    # Act
    tokens = toks(src)
    found = diags(src)

    # Assert — one ERROR token spanning from `/*` to EOF.
    assert [t.kind for t in tokens] == [K.KW_LET, K.IDENT, K.EQ, K.ERROR, K.EOF]
    assert (tokens[3].span.start, tokens[3].span.end) == (8, len(src))
    assert [d.code for d in found] == ["OX0002"]


# §5.6, §5.7, §5.8 — strings (SPEC §3.5)


def test_string_escapes_are_unescaped_into_the_value():
    # Arrange
    src = r'"a\n\t\\\"\u{48}b"'

    # Act
    tokens = toks(src)

    # Assert
    assert [t.kind for t in tokens] == [K.STRING, K.NEWLINE, K.EOF]
    assert tokens[0].value == 'a\n\t\\"Hb'
    assert diags(src) == []
    # \0 plus a 5-hex-digit unicode escape.
    wide = r'"\0\u{1F600}"'
    assert toks(wide)[0].value == "\x00\U0001f600"
    assert diags(wide) == []


def test_invalid_escape_reports_ox0005_and_keeps_the_string_token():
    # Arrange
    src = r'"\q"'

    # Act
    tokens = toks(src)
    found = diags(src)

    # Assert — U+FFFD substituted, scanning continues, kind stays STRING.
    assert tokens[0].kind is K.STRING
    assert "�" in tokens[0].value
    assert [d.code for d in found] == ["OX0005"]


def test_unterminated_string_reports_ox0006_and_resumes():
    # Arrange
    src = 'let s = "abc\nlet t = 1'

    # Act
    tokens = toks(src)
    found = diags(src)

    # Assert — ERROR for the string, then normal lexing on the next line.
    assert [t.kind for t in tokens] == [
        K.KW_LET, K.IDENT, K.EQ, K.ERROR,
        K.KW_LET, K.IDENT, K.EQ, K.INT, K.NEWLINE, K.EOF]
    assert tokens[3].span.start == src.index('"')
    assert tokens[-3].value == 1
    assert [d.code for d in found] == ["OX0006"]


# §5.9, §5.10, §5.11 — numbers (SPEC §3.4)


@pytest.mark.parametrize(
    ("src", "kind", "value"),
    [
        ("42", K.INT, 42), ("1_000", K.INT, 1000), ("0b1010", K.INT, 10),
        ("0o17", K.INT, 15), ("0x1F", K.INT, 31),
        ("1.5", K.FLOAT, 1.5), ("2e3", K.FLOAT, 2000.0),
        ("2E+3", K.FLOAT, 2000.0), ("2e-3", K.FLOAT, 0.002)],
)
def test_numeric_literals_carry_their_parsed_value(src, kind, value):
    # Act
    tokens = toks(src)

    # Assert
    assert [t.kind for t in tokens] == [kind, K.NEWLINE, K.EOF]
    assert tokens[0].lexeme == src
    assert tokens[0].value == value
    assert isinstance(tokens[0].value, float if kind is K.FLOAT else int)
    assert diags(src) == []


def test_dot_after_number_or_ident_lexes_as_dot_not_float():
    # Act / Assert — a float needs a digit after the `.`.
    assert kinds("1.") == [K.INT, K.DOT, K.EOF]
    assert toks("1.")[0].value == 1
    assert kinds("x.0") == [K.IDENT, K.DOT, K.INT, K.NEWLINE, K.EOF]
    assert toks("x.0")[2].value == 0
    # Radix literals are integers only.
    assert kinds("0x1F.0") == [K.INT, K.DOT, K.INT, K.NEWLINE, K.EOF]
    assert diags("1.") == []


def test_numeric_suffix_reports_ox0004_as_one_error_token():
    # Arrange
    src = "123abc"

    # Act
    tokens = toks(src)
    found = diags(src)

    # Assert — the whole alnum run is munched into a single ERROR token.
    assert [t.kind for t in tokens] == [K.ERROR, K.EOF]
    assert tokens[0].lexeme == "123abc"
    assert (tokens[0].span.start, tokens[0].span.end) == (0, len(src))
    assert [d.code for d in found] == ["OX0004"]


@pytest.mark.parametrize("src", ["0x", "0b", "0o"])
def test_empty_radix_digit_run_reports_ox0003(src):
    # Act
    tokens = toks(src)
    found = diags(src)

    # Assert
    assert [t.kind for t in tokens] == [K.ERROR, K.EOF]
    assert [d.code for d in found] == ["OX0003"]


# §5.12, §5.13 — unexpected characters (SPEC §3.6)


def test_unknown_character_reports_ox0001_and_lexing_continues():
    # Arrange
    src = "@ x"

    # Act
    tokens = toks(src)
    found = diags(src)

    # Assert — ERROR token of length 1, then the next token is lexed normally.
    assert [t.kind for t in tokens] == [K.ERROR, K.IDENT, K.NEWLINE, K.EOF]
    assert tokens[0].lexeme == "@"
    assert (tokens[0].span.start, tokens[0].span.end) == (0, 1)
    assert [d.code for d in found] == ["OX0001"]


@pytest.mark.parametrize("op", ["&", "|"])
def test_lone_ampersand_or_pipe_reports_ox0001(op):
    # Arrange
    src = f"a {op} b"

    # Act
    tokens = toks(src)
    found = diags(src)

    # Assert
    assert [t.kind for t in tokens] == [K.IDENT, K.ERROR, K.IDENT, K.NEWLINE, K.EOF]
    assert tokens[1].lexeme == op
    assert [d.code for d in found] == ["OX0001"]


# §5.14 — maximal munch (SPEC §3.6)


@pytest.mark.parametrize(
    ("src", "kind"),
    [
        ("a->b", K.ARROW), ("a=>b", K.FATARROW), ("a==b", K.EQEQ),
        ("a!=b", K.NEQ), ("a<=b", K.LEQ), ("a>=b", K.GEQ),
        ("a&&b", K.ANDAND), ("a||b", K.OROR), ("a::b", K.PATH_SEP)],
)
def test_two_char_operators_win_over_single_char_ones(src, kind):
    # Act / Assert
    assert kinds(src) == [K.IDENT, kind, K.IDENT, K.NEWLINE, K.EOF]
    assert diags(src) == []


@pytest.mark.parametrize(
    ("src", "kind"),
    [
        ("a=b", K.EQ), ("a<b", K.LT), ("a>b", K.GT), ("a+b", K.PLUS),
        ("a-b", K.MINUS), ("a*b", K.STAR), ("a/b", K.SLASH),
        ("a%b", K.PERCENT), ("a!b", K.BANG), ("a.b", K.DOT),
        ("a,b", K.COMMA), ("a:b", K.COLON),
        # v0.2.1 (§34): `?` is a one-char token
        ("a?b", K.QUESTION)],
)
def test_single_char_operators_when_no_two_char_match(src, kind):
    # Act / Assert
    assert kinds(src) == [K.IDENT, kind, K.IDENT, K.NEWLINE, K.EOF]
    assert diags(src) == []


def test_question_mark_lexes_as_question_token_not_ox0001():
    # Arrange — v0.2.1 (§34): `?` is QUESTION, no longer an OX0001 ERROR.
    src = "get(v, 0)?"

    # Act
    tokens = toks(src)

    # Assert — NEWLINE injected before EOF: QUESTION terminates (§36 W2).
    assert [t.kind for t in tokens] == [
        K.IDENT, K.LPAREN, K.IDENT, K.COMMA, K.INT, K.RPAREN,
        K.QUESTION, K.NEWLINE, K.EOF]
    assert tokens[6].lexeme == "?"
    assert (tokens[6].span.start, tokens[6].span.end) == (9, 10)
    assert diags(src) == []


# §5.15 — identifiers & keywords (SPEC §3.3)


@pytest.mark.parametrize(
    ("word", "kind"),
    [
        ("fn", K.KW_FN), ("let", K.KW_LET), ("if", K.KW_IF), ("else", K.KW_ELSE),
        ("while", K.KW_WHILE), ("return", K.KW_RETURN), ("struct", K.KW_STRUCT),
        ("match", K.KW_MATCH), ("true", K.KW_TRUE), ("false", K.KW_FALSE),
        ("for", K.KW_FOR), ("in", K.KW_IN), ("enum", K.KW_ENUM),
        # v0.2.1 keywords (§34)
        ("break", K.KW_BREAK), ("continue", K.KW_CONTINUE)],
)
def test_every_keyword_maps_to_its_keyword_kind(word, kind):
    # Act
    tokens = toks(word)

    # Assert
    assert tokens[0].kind is kind
    assert tokens[0].lexeme == word
    assert diags(word) == []


@pytest.mark.parametrize(
    "word",
    ["fnx", "letter", "iffy", "returns", "_", "_x1", "fore", "int", "enums",
     "breaker", "continues"],
)
def test_keyword_prefixed_words_are_identifiers(word):
    # Act
    tokens = toks(word)

    # Assert — maximal munch first, then one KEYWORDS lookup on the whole run.
    assert tokens[0].kind is K.IDENT
    assert tokens[0].lexeme == word
    assert diags(word) == []


# §5.16 — spans (SPEC §2, §3.2)


def test_token_spans_are_byte_offsets_and_eof_spans_end_of_input():
    # Arrange
    src = "let x = 1"

    # Act
    tokens = toks(src)

    # Assert
    assert [t.kind for t in tokens] == [
        K.KW_LET, K.IDENT, K.EQ, K.INT, K.NEWLINE, K.EOF]
    assert [(t.span.start, t.span.end) for t in tokens[:4]] == [
        (0, 3), (4, 5), (6, 7), (8, 9)]
    assert tokens[-1].kind is K.EOF
    assert tokens[-1].lexeme == ""
    assert (tokens[-1].span.start, tokens[-1].span.end) == (len(src), len(src))


# §5.17 — the lexer never raises (SPEC §2)


@pytest.mark.parametrize(
    "src",
    [
        '"\\u{', "/*/*/*", "\x00\xff@#$", "0x 0b2 9e",
        "", '"abc', "0x_", "1e+", "/* /* */", "\\", '"\\u{ZZ}"',
        # v0.2.1 (§38) garbage inputs
        "?", "..", "break?"],
)
def test_tokenize_never_raises_and_always_ends_with_one_eof(src):
    # Act
    lexer = Lexer(src)
    tokens = lexer.tokenize()

    # Assert
    assert isinstance(tokens, list)
    assert tokens[-1].kind is K.EOF
    assert [t.kind for t in tokens].count(K.EOF) == 1
    assert isinstance(lexer.diagnostics, list)
