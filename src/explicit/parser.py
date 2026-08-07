"""Dialect parser for explicit Oxide (SPEC.md section 41).

Subclasses the unchanged core :class:`src.parser.parser.Parser`, adding
the three dialect surface deltas:

- ``&name`` at expression use sites (parsed as an ordinary ``Var`` node;
  the ``&`` is recorded by node id in :attr:`ExplicitParser.amp_uses`);
- ``name: &Type`` parameter declarations (the ``&`` is recorded by Param
  node id in :attr:`ExplicitParser.amp_params` — dialect syntax only,
  semantics identical to core);
- ``drop name`` statements, parsed as dialect-only :class:`DropStmt`
  nodes that flow through block statement lists and are stripped back
  out (with position records) before the core analysis runs.

The parser never raises: all core recovery machinery is inherited.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.diagnostics import Diagnostic, Span
from src.explicit.lexer import AMP, KW_DROP, ExplicitLexer
from src.lexer.tokens import Token, TokenKind
from src.parser import ast
from src.parser.parser import Parser


@dataclass(frozen=True, slots=True)
class DropStmt:
    """A dialect ``drop name`` statement (never reaches core analysis)."""

    node_id: int
    span: Span
    name: str


class ExplicitParser(Parser):
    """Core parser plus the dialect's ``&`` and ``drop`` productions."""

    def __init__(self, tokens: list[Token]) -> None:
        super().__init__(tokens)
        self.amp_uses: dict[int, Span] = {}  # Var node_id -> span of its '&'
        self.amp_params: dict[int, Span] = {}  # Param node_id -> span of its '&'

    # ---- expressions ------------------------------------------------------

    def _nud(self) -> ast.Expr:
        tok = self._peek()
        if tok.kind is not AMP:
            return super()._nud()
        self._advance()
        name_tok = self._peek()
        if name_tok.kind is not TokenKind.IDENT:
            self._diag(
                "OX0100",
                f"expected variable name after '&', found {name_tok.kind.name}",
                name_tok.span,
            )
            return ast.ErrorExpr(self._new_id(), tok.span)
        self._advance()
        var = ast.Var(
            self._new_id(),
            Span(tok.span.start, name_tok.span.end),
            name_tok.lexeme,
        )
        self.amp_uses[var.node_id] = tok.span
        return var

    # ---- parameters -------------------------------------------------------

    def _param(self) -> ast.Param:
        name_tok = self._expect(TokenKind.IDENT, "parameter name")
        ty: ast.TypeExpr | None = None
        amp_span: Span | None = None
        if self._match(TokenKind.COLON):
            amp_tok = self._match(AMP)  # type: ignore[arg-type]
            if amp_tok is not None:
                amp_span = amp_tok.span
            ty = self._type()
        end = ty.span.end if ty is not None else name_tok.span.end
        param = ast.Param(
            self._new_id(), Span(name_tok.span.start, end), name_tok.lexeme, ty
        )
        if amp_span is not None:
            self.amp_params[param.node_id] = amp_span
        return param

    # ---- statements -------------------------------------------------------

    def _statement(self) -> ast.Stmt:
        if self._peek().kind is KW_DROP:
            return self._drop_stmt()  # type: ignore[return-value]
        return super()._statement()

    def _drop_stmt(self) -> DropStmt:
        kw = self._advance()
        name_tok = self._expect(TokenKind.IDENT, "variable name after 'drop'")
        self._expect_term()
        return DropStmt(
            self._new_id(),
            Span(kw.span.start, name_tok.span.end),
            name_tok.lexeme,
        )


def parse_explicit(
    source: str,
) -> tuple[ast.Module, list[Diagnostic], ExplicitParser]:
    """Dialect lex + parse. Diagnostics = lexer's, then parser's.

    Never raises; the returned parser instance carries the ``&`` records.
    """
    lexer = ExplicitLexer(source)
    tokens = lexer.tokenize()
    parser = ExplicitParser(tokens)
    module = parser.parse_module()
    return module, [*lexer.diagnostics, *parser.diagnostics], parser
