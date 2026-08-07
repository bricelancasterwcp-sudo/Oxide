"""The Oxide lexer.

Converts Oxide source text into a flat list of Token objects, following the
normative rules in SPEC.md §3. The lexer never raises: any malformed input
becomes an ERROR token plus a queued Diagnostic, and scanning always
continues to the end of the source.
"""

import sys

from src.diagnostics import Diagnostic, Span
from src.lexer.tokens import KEYWORDS, TERMINATOR_SET, Token, TokenKind

_TWO_CHAR_OPERATORS: dict[str, TokenKind] = {
    "->": TokenKind.ARROW,
    "=>": TokenKind.FATARROW,
    "==": TokenKind.EQEQ,
    "!=": TokenKind.NEQ,
    "<=": TokenKind.LEQ,
    ">=": TokenKind.GEQ,
    "&&": TokenKind.ANDAND,
    "||": TokenKind.OROR,
    "::": TokenKind.PATH_SEP,
}

_ONE_CHAR_OPERATORS: dict[str, TokenKind] = {
    "=": TokenKind.EQ,
    "<": TokenKind.LT,
    ">": TokenKind.GT,
    "+": TokenKind.PLUS,
    "-": TokenKind.MINUS,
    "*": TokenKind.STAR,
    "/": TokenKind.SLASH,
    "%": TokenKind.PERCENT,
    "!": TokenKind.BANG,
    ".": TokenKind.DOT,
    "?": TokenKind.QUESTION,
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    "{": TokenKind.LBRACE,
    "}": TokenKind.RBRACE,
    ",": TokenKind.COMMA,
    ":": TokenKind.COLON,
}

_SIMPLE_STRING_ESCAPES: dict[str, str] = {
    "n": "\n",
    "t": "\t",
    "\\": "\\",
    '"': '"',
    "0": "\0",
}

_HEX_DIGITS = "0123456789abcdefABCDEF"
_RADIX_DIGIT_SETS: dict[str, str] = {
    "x": _HEX_DIGITS,
    "o": "01234567",
    "b": "01",
}
_RADIX_VALUES: dict[str, int] = {"x": 16, "o": 8, "b": 2}

_MAX_CODEPOINT = 0x10FFFF
_SURROGATE_LOW = 0xD800
_SURROGATE_HIGH = 0xDFFF


def _is_ascii_alpha(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def _is_ascii_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _is_ident_continue(ch: str) -> bool:
    return _is_ascii_alpha(ch) or _is_ascii_digit(ch) or ch == "_"


def _is_valid_codepoint(codepoint: int) -> bool:
    return codepoint <= _MAX_CODEPOINT and not (
        _SURROGATE_LOW <= codepoint <= _SURROGATE_HIGH
    )


class Lexer:
    """Scans Oxide source text into a list of Tokens; never raises."""

    def __init__(self, source: str) -> None:
        self.src: str = source
        self.pos: int = 0
        self.tokens: list[Token] = []
        self.diagnostics: list[Diagnostic] = []
        self.prev_kind: TokenKind | None = None

    def tokenize(self) -> list[Token]:
        """Scan the whole source and return the token list, always ending in EOF."""
        n = len(self.src)
        while self.pos < n:
            ch = self.src[self.pos]
            if ch in " \t\r":
                self.pos += 1
                continue
            if ch == "\n":
                self._handle_newline()
                continue
            if ch == "/" and self._peek(1) == "/":
                self._skip_line_comment()
                continue
            if ch == "/" and self._peek(1) == "*":
                self._skip_block_comment()
                continue
            token = self._scan_token(ch)
            self.tokens.append(token)
            self.prev_kind = token.kind
        self._emit_eof()
        return self.tokens

    def _scan_token(self, ch: str) -> Token:
        if _is_ascii_alpha(ch) or ch == "_":
            return self._scan_ident_or_keyword()
        if _is_ascii_digit(ch):
            return self._scan_number()
        if ch == '"':
            return self._scan_string()
        return self._scan_operator()

    def _peek(self, offset: int) -> str:
        idx = self.pos + offset
        if 0 <= idx < len(self.src):
            return self.src[idx]
        return ""

    def _add_diag(self, code: str, message: str, span: Span) -> None:
        self.diagnostics.append(Diagnostic(code=code, message=message, span=span))

    # ---- newline / EOF -----------------------------------------------

    def _handle_newline(self) -> None:
        start = self.pos
        if self.prev_kind in TERMINATOR_SET:
            token = Token(TokenKind.NEWLINE, "\n", Span(start, start + 1))
            self.tokens.append(token)
            self.prev_kind = TokenKind.NEWLINE
        self.pos = start + 1

    def _emit_eof(self) -> None:
        end = len(self.src)
        if self.prev_kind in TERMINATOR_SET:
            newline_token = Token(TokenKind.NEWLINE, "", Span(end, end))
            self.tokens.append(newline_token)
            self.prev_kind = TokenKind.NEWLINE
        eof_token = Token(TokenKind.EOF, "", Span(end, end))
        self.tokens.append(eof_token)
        self.prev_kind = TokenKind.EOF

    # ---- comments -------------------------------------------------------

    def _skip_line_comment(self) -> None:
        n = len(self.src)
        self.pos += 2
        while self.pos < n and self.src[self.pos] != "\n":
            self.pos += 1

    def _skip_block_comment(self) -> None:
        start = self.pos
        n = len(self.src)
        self.pos += 2
        depth = 1
        while depth > 0:
            if self.pos >= n:
                self._add_diag(
                    "OX0002", "unterminated block comment", Span(start, n)
                )
                token = Token(TokenKind.ERROR, self.src[start:n], Span(start, n))
                self.tokens.append(token)
                self.prev_kind = TokenKind.ERROR
                self.pos = n
                return
            two = self.src[self.pos : self.pos + 2]
            if two == "/*":
                depth += 1
                self.pos += 2
            elif two == "*/":
                depth -= 1
                self.pos += 2
            else:
                self.pos += 1

    # ---- identifiers & keywords ------------------------------------------

    def _scan_ident_or_keyword(self) -> Token:
        start = self.pos
        n = len(self.src)
        pos = start
        while pos < n and _is_ident_continue(self.src[pos]):
            pos += 1
        text = self.src[start:pos]
        self.pos = pos
        if text in KEYWORDS:
            return Token(KEYWORDS[text], text, Span(start, pos))
        return Token(TokenKind.IDENT, sys.intern(text), Span(start, pos))

    # ---- numbers ----------------------------------------------------------

    def _scan_number(self) -> Token:
        start = self.pos
        n = len(self.src)
        if (
            self.src[start] == "0"
            and start + 1 < n
            and self.src[start + 1] in _RADIX_DIGIT_SETS
        ):
            return self._scan_radix_number(start)
        return self._scan_decimal_number(start)

    def _scan_radix_number(self, start: int) -> Token:
        n = len(self.src)
        radix_char = self.src[start + 1]
        digit_set = _RADIX_DIGIT_SETS[radix_char]
        radix = _RADIX_VALUES[radix_char]
        pos = start + 2
        digit_start = pos
        saw_digit = False
        while pos < n and (self.src[pos] in digit_set or self.src[pos] == "_"):
            if self.src[pos] != "_":
                saw_digit = True
            pos += 1
        if not saw_digit:
            return self._finalize_number_error(
                start, pos, "OX0003", "malformed numeric literal: empty digit run"
            )
        digits_text = self.src[digit_start:pos].replace("_", "")
        value = int(digits_text, radix)
        return self._finalize_number_success(start, pos, TokenKind.INT, value)

    def _scan_decimal_number(self, start: int) -> Token:
        n = len(self.src)
        pos = start
        while pos < n and (_is_ascii_digit(self.src[pos]) or self.src[pos] == "_"):
            pos += 1
        is_float = False
        if (
            pos < n
            and self.src[pos] == "."
            and pos + 1 < n
            and _is_ascii_digit(self.src[pos + 1])
        ):
            is_float = True
            pos += 1
            while pos < n and (
                _is_ascii_digit(self.src[pos]) or self.src[pos] == "_"
            ):
                pos += 1
        if pos < n and self.src[pos] in ("e", "E"):
            exp_pos = pos + 1
            if exp_pos < n and self.src[exp_pos] in ("+", "-"):
                exp_pos += 1
            exp_digits_start = exp_pos
            while exp_pos < n and _is_ascii_digit(self.src[exp_pos]):
                exp_pos += 1
            if exp_pos == exp_digits_start:
                return self._finalize_number_error(
                    start, exp_pos, "OX0003", "malformed numeric literal: empty exponent"
                )
            is_float = True
            pos = exp_pos
        digits_text = self.src[start:pos].replace("_", "")
        if is_float:
            return self._finalize_number_success(
                start, pos, TokenKind.FLOAT, float(digits_text)
            )
        return self._finalize_number_success(start, pos, TokenKind.INT, int(digits_text))

    def _extend_for_adjacency(self, pos: int) -> int:
        n = len(self.src)
        if pos < n and (_is_ascii_alpha(self.src[pos]) or self.src[pos] == "_"):
            end = pos
            while end < n and _is_ident_continue(self.src[end]):
                end += 1
            return end
        return pos

    def _finalize_number_success(
        self, start: int, pos: int, kind: TokenKind, value: int | float
    ) -> Token:
        end = self._extend_for_adjacency(pos)
        self.pos = end
        if end != pos:
            self._add_diag(
                "OX0004", "invalid suffix on numeric literal", Span(start, end)
            )
            return Token(TokenKind.ERROR, self.src[start:end], Span(start, end))
        return Token(kind, self.src[start:pos], Span(start, pos), value)

    def _finalize_number_error(
        self, start: int, pos: int, code: str, message: str
    ) -> Token:
        end = self._extend_for_adjacency(pos)
        self.pos = end
        if end != pos:
            self._add_diag(
                "OX0004", "invalid suffix on numeric literal", Span(start, end)
            )
        else:
            self._add_diag(code, message, Span(start, end))
        return Token(TokenKind.ERROR, self.src[start:end], Span(start, end))

    # ---- strings ------------------------------------------------------------

    def _scan_string(self) -> Token:
        start = self.pos
        n = len(self.src)
        pos = start + 1
        chars: list[str] = []
        while True:
            if pos >= n or self.src[pos] == "\n":
                return self._unterminated_string(start, pos)
            ch = self.src[pos]
            if ch == '"':
                pos += 1
                self.pos = pos
                value = "".join(chars)
                return Token(TokenKind.STRING, self.src[start:pos], Span(start, pos), value)
            if ch != "\\":
                chars.append(ch)
                pos += 1
                continue
            new_pos, unterminated_at = self._scan_string_escape(pos, chars)
            if unterminated_at is not None:
                return self._unterminated_string(start, unterminated_at)
            pos = new_pos

    def _scan_string_escape(
        self, backslash_pos: int, chars: list[str]
    ) -> tuple[int, int | None]:
        """Consume one escape sequence starting at `backslash_pos`.

        Returns (new_pos, unterminated_at). `unterminated_at` is None on a
        normal escape, or the position of the EOF/newline that cut the
        string short if the escape could not be completed.
        """
        n = len(self.src)
        esc_start = backslash_pos
        pos = backslash_pos + 1
        if pos >= n or self.src[pos] == "\n":
            return pos, pos
        esc = self.src[pos]
        if esc in _SIMPLE_STRING_ESCAPES:
            chars.append(_SIMPLE_STRING_ESCAPES[esc])
            return pos + 1, None
        if esc == "u":
            new_pos, ch_value, ok = self._scan_unicode_escape(pos + 1)
            if ok:
                chars.append(ch_value)
            else:
                self._add_diag(
                    "OX0005", "invalid escape sequence", Span(esc_start, new_pos)
                )
                chars.append("�")
            return new_pos, None
        self._add_diag("OX0005", "invalid escape sequence", Span(esc_start, pos + 1))
        chars.append("�")
        return pos + 1, None

    def _scan_unicode_escape(self, pos_after_u: int) -> tuple[int, str, bool]:
        """Scan `{H..H}` (1-6 hex digits) after `\\u`. Returns (new_pos, char, ok)."""
        n = len(self.src)
        if pos_after_u >= n or self.src[pos_after_u] != "{":
            return pos_after_u, "", False
        hex_start = pos_after_u + 1
        end = hex_start
        while end < n and end - hex_start < 6 and self.src[end] in _HEX_DIGITS:
            end += 1
        if end == hex_start:
            return end, "", False
        if end >= n or self.src[end] != "}":
            return end, "", False
        codepoint = int(self.src[hex_start:end], 16)
        if not _is_valid_codepoint(codepoint):
            return end + 1, "", False
        return end + 1, chr(codepoint), True

    def _unterminated_string(self, start: int, terminator_pos: int) -> Token:
        n = len(self.src)
        self._add_diag(
            "OX0006", "unterminated string literal", Span(start, terminator_pos)
        )
        token = Token(
            TokenKind.ERROR,
            self.src[start:terminator_pos],
            Span(start, terminator_pos),
        )
        if terminator_pos < n:
            self.pos = terminator_pos + 1
        else:
            self.pos = terminator_pos
        return token

    # ---- operators & delimiters ----------------------------------------------

    def _scan_operator(self) -> Token:
        start = self.pos
        two = self.src[start : start + 2]
        if two in _TWO_CHAR_OPERATORS:
            self.pos = start + 2
            return Token(_TWO_CHAR_OPERATORS[two], two, Span(start, start + 2))
        one = self.src[start]
        if one in _ONE_CHAR_OPERATORS:
            self.pos = start + 1
            return Token(_ONE_CHAR_OPERATORS[one], one, Span(start, start + 1))
        self.pos = start + 1
        self._add_diag(
            "OX0001", f"unexpected character {one!r}", Span(start, start + 1)
        )
        return Token(TokenKind.ERROR, one, Span(start, start + 1))
