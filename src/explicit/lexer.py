"""Dialect lexer for explicit Oxide (SPEC.md section 41).

Two surface deltas over the core lexer, both dialect-only: a single ``&``
lexes as an AMP token (in core it is an OX0001 error), and ``drop`` is a
keyword rather than an identifier. Everything else — ``&&``, NEWLINE
emission, the terminator set, error recovery — is inherited unchanged
from :class:`src.lexer.lexer.Lexer` (core files are not modified).
"""

from __future__ import annotations

from enum import Enum, auto

from src.diagnostics import Span
from src.lexer.lexer import Lexer
from src.lexer.tokens import Token, TokenKind


class ExplicitTokenKind(Enum):
    """Dialect-only token kinds (disjoint from the core TokenKind)."""

    AMP = auto()  # single '&' — the dialect's read-use / read-param marker
    KW_DROP = auto()  # 'drop' — the dialect's explicit-destruction keyword


AMP = ExplicitTokenKind.AMP
KW_DROP = ExplicitTokenKind.KW_DROP


class ExplicitLexer(Lexer):
    """Core lexer plus the dialect's AMP token and ``drop`` keyword.

    Neither AMP nor KW_DROP is a statement terminator, so the inherited
    NEWLINE rules are unaffected (``&&`` still wins maximal munch).
    """

    def _scan_operator(self) -> Token:
        start = self.pos
        if self.src[start] == "&" and self._peek(1) != "&":
            self.pos = start + 1
            return Token(AMP, "&", Span(start, start + 1))  # type: ignore[arg-type]
        return super()._scan_operator()

    def _scan_ident_or_keyword(self) -> Token:
        token = super()._scan_ident_or_keyword()
        if token.kind is TokenKind.IDENT and token.lexeme == "drop":
            return Token(KW_DROP, "drop", token.span)  # type: ignore[arg-type]
        return token
