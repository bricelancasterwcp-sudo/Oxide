"""Expression tier of the Oxide parser: Pratt loop, nud dispatch, postfix
operators, if/match expressions, and struct literals (SPEC.md §§6, 10, as
amended by Part V §26 and Part VII §34).

``_ExprParserMixin`` is mixed into :class:`src.parser.parser.Parser`, which
owns all state (token cursor, flags, diagnostics) and the shared primitives
(``_peek``/``_advance``/``_expect``/``_fail``/``_enter_nested``/...). The
split exists purely to keep both modules within the 800-line file budget.
"""

from __future__ import annotations

from dataclasses import replace

from src.diagnostics import Diagnostic, Span
from src.lexer.tokens import Token, TokenKind
from src.parser.ast import (
    BinOp,
    Block,
    Call,
    ErrorExpr,
    Expr,
    FieldAccess,
    If,
    Lit,
    Match,
    MatchArm,
    StructLit,
    Try,
    UnOp,
    Var,
    VariantPat,
)

#: Builtin names callable with receiver-first method syntax (SPEC.md §53):
#: `recv.name(args)` parses as `name(recv, args)`. Mirrored from
#: ``src.sema.types.BUILTINS``; the parser must not import sema (that would
#: invert the layering), so ``test_parser.py`` asserts the two stay in sync.
BUILTIN_METHOD_NAMES: frozenset[str] = frozenset(
    {
        "chars",
        "clone",
        "concat",
        "get",
        "int_to_str",
        "len",
        "parse_int",
        "print",
        "print_str",
        "push",
        "range",
        "str_len",
        "to_float",
        "trunc",
        "vec",
    }
)

_BINARY_BP: dict[TokenKind, tuple[int, int]] = {
    TokenKind.OROR: (1, 2),
    TokenKind.ANDAND: (3, 4),
    TokenKind.EQEQ: (5, 6),
    TokenKind.NEQ: (5, 6),
    TokenKind.LT: (5, 6),
    TokenKind.LEQ: (5, 6),
    TokenKind.GT: (5, 6),
    TokenKind.GEQ: (5, 6),
    TokenKind.PLUS: (7, 8),
    TokenKind.MINUS: (7, 8),
    TokenKind.STAR: (9, 10),
    TokenKind.SLASH: (9, 10),
    TokenKind.PERCENT: (9, 10),
}

_COMPARISON_OPS: frozenset[TokenKind] = frozenset(
    {
        TokenKind.EQEQ,
        TokenKind.NEQ,
        TokenKind.LT,
        TokenKind.LEQ,
        TokenKind.GT,
        TokenKind.GEQ,
    }
)

_PREFIX_RBP = 11
_POSTFIX_LBP = 13

_POSTFIX_STARTERS: tuple[TokenKind, ...] = (
    TokenKind.DOT,
    TokenKind.LPAREN,
    TokenKind.QUESTION,
)

_LIT_KINDS: dict[TokenKind, str] = {
    TokenKind.INT: "int",
    TokenKind.FLOAT: "float",
    TokenKind.STRING: "str",
}


class _ExprParserMixin:
    """Expression-parsing methods; state and primitives live on Parser."""

    # ---- expressions (Pratt) ----------------------------------------------

    def _parse_expr(self, min_bp: int) -> Expr:
        self._enter_nested()
        try:
            return self._parse_expr_inner(min_bp)
        finally:
            self._depth -= 1

    def _parse_expr_inner(self, min_bp: int) -> Expr:
        self._peek()  # normalize position past any skippable NEWLINEs
        start_idx = self.pos
        lhs = self._nud()
        if self.pos == start_idx:
            # nud failed without consuming a token (OX0100 already reported).
            # Stop here so one broken region yields one diagnostic instead of
            # cascading through the operator loop (SPEC.md §11).
            return lhs
        seen_comparison = False
        reported_chain = False
        while True:
            kind = self._peek().kind
            if kind in _POSTFIX_STARTERS and _POSTFIX_LBP >= min_bp:
                lhs = self._postfix(lhs)
                continue
            bp = _BINARY_BP.get(kind)
            if bp is None or bp[0] < min_bp:
                break
            if kind in _COMPARISON_OPS:
                if seen_comparison and not reported_chain:
                    self.diagnostics.append(
                        Diagnostic(
                            "OX0110",
                            "comparison operators cannot be chained",
                            self._peek().span,
                        )
                    )
                    reported_chain = True
                seen_comparison = True
            op_tok = self._advance()
            rhs = self._parse_expr(bp[1])
            span = Span(lhs.span.start, rhs.span.end)
            lhs = BinOp(self._new_id(), span, op_tok.lexeme, lhs, rhs)
        return lhs

    def _nud(self) -> Expr:
        tok = self._peek()
        kind = tok.kind
        if kind in _LIT_KINDS:
            self._advance()
            return Lit(self._new_id(), tok.span, tok.value, _LIT_KINDS[kind])
        if kind is TokenKind.KW_TRUE or kind is TokenKind.KW_FALSE:
            self._advance()
            return Lit(self._new_id(), tok.span, kind is TokenKind.KW_TRUE, "bool")
        if kind is TokenKind.IDENT:
            self._advance()
            if self._check(TokenKind.LBRACE) and not self._no_struct_lit:
                return self._struct_lit(tok)
            return Var(self._new_id(), tok.span, tok.lexeme)
        if kind is TokenKind.MINUS or kind is TokenKind.BANG:
            op_tok = self._advance()
            operand = self._parse_expr(_PREFIX_RBP)
            span = Span(op_tok.span.start, operand.span.end)
            return UnOp(self._new_id(), span, op_tok.lexeme, operand)
        if kind is TokenKind.LPAREN:
            return self._paren_group()
        if kind is TokenKind.KW_IF:
            return self._if_expr()
        if kind is TokenKind.KW_MATCH:
            return self._match_expr()
        if kind is TokenKind.ERROR:
            # The lexer already reported this token; no cascaded diagnostic.
            self._advance()
            return ErrorExpr(self._new_id(), tok.span)
        self._diag("OX0100", f"expected expression, found {kind.name}", tok.span)
        return ErrorExpr(self._new_id(), Span(tok.span.start, tok.span.start))

    def _paren_group(self) -> Expr:
        lparen = self._advance()  # (
        saved = (self._skip_nl, self._no_struct_lit)
        self._skip_nl = True
        self._no_struct_lit = False
        inner = self._parse_expr(0)
        rparen = self._expect(TokenKind.RPAREN, "')'")
        self._skip_nl, self._no_struct_lit = saved
        # Widen the node's extent to the paren pair so enclosing spans stay
        # token-balanced (never start or end inside the parentheses).
        return replace(inner, span=Span(lparen.span.start, rparen.span.end))

    def _if_expr(self) -> If:
        self._enter_nested()
        try:
            return self._if_expr_inner()
        finally:
            self._depth -= 1

    def _if_expr_inner(self) -> If:
        kw = self._advance()  # if
        saved = self._no_struct_lit
        self._no_struct_lit = True
        cond = self._parse_expr(0)
        self._no_struct_lit = saved
        then_blk = self._block()
        else_blk: Block | If | None = None
        # v0.2.1 newline tolerance (SPEC.md §34): a NEWLINE run between `}`
        # and `else` is skipped. Probe past the run; if no `else` follows,
        # restore so the NEWLINE still terminates the enclosing statement.
        probe = self.pos
        self._skip_newline_run()
        if self._match(TokenKind.KW_ELSE):
            else_blk = self._if_expr() if self._check(TokenKind.KW_IF) else self._block()
        else:
            self.pos = probe
        end = (else_blk if else_blk is not None else then_blk).span.end
        return If(self._new_id(), Span(kw.span.start, end), cond, then_blk, else_blk)

    def _match_expr(self) -> Match:
        self._enter_nested()
        try:
            return self._match_expr_inner()
        finally:
            self._depth -= 1

    def _match_expr_inner(self) -> Match:
        kw = self._advance()  # match
        saved = (self._skip_nl, self._no_struct_lit)
        self._no_struct_lit = True  # §6 restriction extends to match scrutinees
        scrutinee = self._parse_expr(0)
        self._no_struct_lit = saved[1]
        # v0.2.1 newline tolerance (SPEC.md §34): match header to its `{`.
        self._skip_newline_run()
        self._expect(TokenKind.LBRACE, "'{'")
        # NEWLINEs are skipped inside the match-arm braces (SPEC.md §26); arm
        # bodies are ordinary expression/block contexts, so struct literals
        # are allowed again.
        self._skip_nl = True
        self._no_struct_lit = False
        arms: list[MatchArm] = []
        while not self._check(TokenKind.RBRACE):
            arms.append(self._match_arm())
            if not self._match(TokenKind.COMMA):
                break
        rbrace = self._expect(TokenKind.RBRACE, "'}'")
        self._skip_nl, self._no_struct_lit = saved
        span = Span(kw.span.start, rbrace.span.end)
        return Match(self._new_id(), span, scrutinee, tuple(arms))

    def _match_arm(self) -> MatchArm:
        pat = self._arm_pat()
        self._expect(TokenKind.FATARROW, "'=>'")
        body: Expr | Block
        if self._check(TokenKind.LBRACE):
            body = self._block()
        else:
            body = self._parse_expr(0)
        span = Span(pat.span.start, body.span.end)
        return MatchArm(self._new_id(), span, pat, body)

    def _arm_pat(self) -> VariantPat:
        tok = self._peek()
        if tok.kind is not TokenKind.IDENT:
            self._fail(
                "OX0104", f"expected match arm pattern, found {tok.kind.name}", tok.span
            )
        name_tok = self._advance()
        # `_` is a wildcard ONLY as a whole arm_pat (SPEC.md §26); `_(...)`
        # falls through as a variant pattern named `_` for sema to reject.
        if name_tok.lexeme == "_" and not self._check(TokenKind.LPAREN):
            return VariantPat(self._new_id(), name_tok.span, None, ())
        binders: list[str] = []
        end = name_tok.span.end
        if self._match(TokenKind.LPAREN):
            # Grammar: "(" IDENT ("," IDENT)* ")" — at least one binder, no
            # trailing comma inside the pattern parens.
            binders.append(self._expect(TokenKind.IDENT, "binder name").lexeme)
            while self._match(TokenKind.COMMA):
                binders.append(self._expect(TokenKind.IDENT, "binder name").lexeme)
            end = self._expect(TokenKind.RPAREN, "')'").span.end
        return VariantPat(
            self._new_id(),
            Span(name_tok.span.start, end),
            name_tok.lexeme,
            tuple(binders),
        )

    def _struct_lit(self, name_tok: Token) -> StructLit:
        self._advance()  # {
        saved = (self._skip_nl, self._no_struct_lit)
        self._skip_nl = True
        self._no_struct_lit = False
        fields: list[tuple[str, Expr]] = []
        rest: Expr | None = None
        while not self._check(TokenKind.RBRACE):
            if self._check(TokenKind.DOT):
                # v0.2.1 functional update (SPEC.md §34): `..rest`, which must
                # be LAST; `Point { ..p }` (no listed fields) is legal. `..`
                # is two DOT tokens (the lexer has no two-char `..`).
                self._advance()
                self._expect(TokenKind.DOT, "'.'")
                rest = self._parse_expr(0)
                self._match(TokenKind.COMMA)  # optional trailing comma
                break
            fname = self._expect(TokenKind.IDENT, "field name")
            self._expect(TokenKind.COLON, "':'")
            fields.append((fname.lexeme, self._parse_expr(0)))
            if not self._match(TokenKind.COMMA):
                break
        rbrace = self._expect(TokenKind.RBRACE, "'}'")
        self._skip_nl, self._no_struct_lit = saved
        span = Span(name_tok.span.start, rbrace.span.end)
        return StructLit(self._new_id(), span, name_tok.lexeme, tuple(fields), rest)

    def _builtin_method(self, receiver: Expr, name_tok: Token) -> Expr:
        """Desugar `recv.name(args)` to `name(recv, args)` (SPEC.md §53).

        Every mainstream language writes a receiver-first call as
        `recv.method(...)`; Oxide's builtins are prefix functions. Measured
        on the ownership probe, 82% of failing Oxide repairs contained
        `.clone()` -- the single largest failure mode, and the only Rust
        idiom the language card failed to suppress (`let mut`, `;`,
        `vec![]` and indexing appeared zero times). This is sugar only:
        the desugared call is an ordinary Call node, so name resolution,
        use-context classification, linearity and codegen all see exactly
        what they would have seen for `name(recv, ...)`.

        Restricted to builtin names. `p.x()` where `x` is a struct field
        stays a field access followed by a call -- an error, as before,
        since Oxide has no callable fields.
        """
        self._advance()  # LPAREN
        saved = (self._skip_nl, self._no_struct_lit)
        self._skip_nl = True
        self._no_struct_lit = False
        args: list[Expr] = [receiver]
        while not self._check(TokenKind.RPAREN):
            args.append(self._parse_expr(0))
            if not self._match(TokenKind.COMMA):
                break
        rparen = self._expect(TokenKind.RPAREN, "')'")
        self._skip_nl, self._no_struct_lit = saved
        span = Span(receiver.span.start, rparen.span.end)
        callee = Var(self._new_id(), name_tok.span, name_tok.lexeme)
        return Call(self._new_id(), span, callee, tuple(args))

    def _postfix(self, lhs: Expr) -> Expr:
        tok = self._advance()  # DOT, LPAREN, or QUESTION
        if tok.kind is TokenKind.QUESTION:
            # Postfix `?` (SPEC.md §§34-35): Try at the postfix tier (lbp 13).
            return Try(self._new_id(), Span(lhs.span.start, tok.span.end), lhs)
        if tok.kind is TokenKind.DOT:
            name_tok = self._expect(TokenKind.IDENT, "field name")
            if name_tok.lexeme in BUILTIN_METHOD_NAMES and self._check(
                TokenKind.LPAREN
            ):
                return self._builtin_method(lhs, name_tok)
            span = Span(lhs.span.start, name_tok.span.end)
            return FieldAccess(self._new_id(), span, lhs, name_tok.lexeme)
        saved = (self._skip_nl, self._no_struct_lit)
        self._skip_nl = True
        self._no_struct_lit = False
        args: list[Expr] = []
        while not self._check(TokenKind.RPAREN):
            args.append(self._parse_expr(0))
            if not self._match(TokenKind.COMMA):
                break
        rparen = self._expect(TokenKind.RPAREN, "')'")
        self._skip_nl, self._no_struct_lit = saved
        span = Span(lhs.span.start, rparen.span.end)
        return Call(self._new_id(), span, lhs, tuple(args))
