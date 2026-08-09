"""Recursive-descent + Pratt parser for Oxide (SPEC.md Part II, §§6, 9-11,
as amended by Part V §§26-27: enums/match, for/in, assignment; and by
Part VII §§34-35: break/continue, postfix `?`, struct-literal `..rest`,
newline tolerance before a body's `{` and before `else`).

The parser never raises on any token stream: failures are recorded as
Diagnostics and recovered via panic-mode synchronization, yielding
ErrorExpr/ErrorStmt sentinel nodes with real node ids and spans.
"""

from __future__ import annotations

from src.diagnostics import Diagnostic, Span
from src.lexer.lexer import Lexer
from src.lexer.tokens import Token, TokenKind
from src.parser.ast import (
    Assign,
    BindPat,
    Block,
    Break,
    Continue,
    DestructPat,
    EnumDecl,
    ErrorStmt,
    Expr,
    ExprStmt,
    FieldAssign,
    FieldDef,
    FnDecl,
    For,
    Item,
    Let,
    Module,
    Param,
    Pattern,
    Return,
    Stmt,
    StructDecl,
    TypeExpr,
    While,
)
from src.parser.expressions import _ExprParserMixin

# Nesting cap for recursive productions: parse_source must never raise
# (SPEC.md §9), so pathological nesting fails with a diagnostic well before
# the Python recursion limit instead of escaping as a RecursionError.
_MAX_DEPTH = 100

_SYNC_SET: frozenset[TokenKind] = frozenset(
    {
        TokenKind.NEWLINE,
        TokenKind.RBRACE,
        TokenKind.KW_LET,
        TokenKind.KW_RETURN,
        TokenKind.KW_WHILE,
        TokenKind.KW_IF,
        TokenKind.KW_FN,
        TokenKind.KW_STRUCT,
        TokenKind.EOF,
    }
)

_ITEM_START: frozenset[TokenKind] = frozenset(
    {TokenKind.KW_FN, TokenKind.KW_STRUCT, TokenKind.KW_ENUM, TokenKind.EOF}
)

_TERM_LOOKAHEAD: frozenset[TokenKind] = frozenset(
    {TokenKind.NEWLINE, TokenKind.RBRACE, TokenKind.EOF}
)


class _ParseError(Exception):
    """Internal control-flow signal for panic-mode recovery; never escapes."""


class Parser(_ExprParserMixin):
    """Parses a token stream into a Module; never raises (SPEC.md §9).

    Statement, item, and recovery machinery lives here; the Pratt expression
    tier is inherited from :class:`_ExprParserMixin` (same instance state).
    """

    def __init__(self, tokens: list[Token]) -> None:
        toks = list(tokens)
        if not toks or toks[-1].kind is not TokenKind.EOF:
            end = toks[-1].span.end if toks else 0
            toks.append(Token(TokenKind.EOF, "", Span(end, end)))
        self.tokens: list[Token] = toks
        self.pos: int = 0
        self.diagnostics: list[Diagnostic] = []
        self._next_id: int = 0
        self._skip_nl: bool = False
        self._no_struct_lit: bool = False
        self._last_diag_pos: int = -1
        self._depth: int = 0
        # v0.2.1 (SPEC.md §34): while/for body nesting depth for OX0105
        # (`break`/`continue` outside a loop); reset at fn boundaries.
        self._loop_depth: int = 0

    # ---- token primitives -------------------------------------------------

    def _new_id(self) -> int:
        node_id = self._next_id
        self._next_id += 1
        return node_id

    def _peek(self) -> Token:
        if self._skip_nl:
            while self.tokens[self.pos].kind is TokenKind.NEWLINE:
                self.pos += 1
        return self.tokens[self.pos]

    def _advance(self) -> Token:
        tok = self._peek()
        if tok.kind is not TokenKind.EOF:
            self.pos += 1
        return tok

    def _check(self, kind: TokenKind) -> bool:
        return self._peek().kind is kind

    def _peek_next(self) -> Token:
        """The raw token after the current one (no NEWLINE skipping past it).

        Used only for the assignment lookahead ``IDENT EQ`` (SPEC.md §26),
        which must not see through a NEWLINE: ``x`` at end of line is an
        expression statement, never the start of an assignment.
        """
        tok = self._peek()  # normalizes self.pos
        if tok.kind is TokenKind.EOF:
            return tok
        return self.tokens[self.pos + 1]

    def _peek_raw(self) -> Token:
        """The token at the cursor with NO NEWLINE skipping.

        The §56 field-assignment scan must stop at end of line, for the
        same reason ``_peek_next`` must: an identifier at end of line is an
        expression statement, never the start of an assignment.
        """
        return self.tokens[self.pos]

    def _match(self, kind: TokenKind) -> Token | None:
        if self._check(kind):
            return self._advance()
        return None

    def _skip_newline_run(self) -> None:
        while self.tokens[self.pos].kind is TokenKind.NEWLINE:
            self.pos += 1

    # ---- diagnostics & recovery -------------------------------------------

    def _diag(self, code: str, message: str, span: Span) -> None:
        """Record a diagnostic unless one was already reported at this token."""
        if self._last_diag_pos == self.pos:
            return
        self._last_diag_pos = self.pos
        self.diagnostics.append(Diagnostic(code, message, span))

    def _fail(self, code: str, message: str, span: Span) -> None:
        self._diag(code, message, span)
        raise _ParseError

    def _enter_nested(self) -> None:
        """Depth guard for recursive productions; pairs with a finally-decrement.

        Raises (with a diagnostic) before incrementing, so callers only
        decrement after a successful entry.
        """
        if self._depth >= _MAX_DEPTH:
            self._fail("OX0101", "nesting too deep", self._peek().span)
        self._depth += 1

    def _expect(self, kind: TokenKind, what: str) -> Token:
        tok = self._peek()
        if tok.kind is kind:
            return self._advance()
        self._fail("OX0101", f"expected {what}, found {tok.kind.name}", tok.span)
        raise AssertionError("unreachable")  # pragma: no cover

    def _expect_term(self) -> None:
        """TERM = NEWLINE (consumed) or lookahead RBRACE / EOF (left alone)."""
        tok = self._peek()
        if tok.kind is TokenKind.NEWLINE:
            self._advance()
            return
        if tok.kind in (TokenKind.RBRACE, TokenKind.EOF):
            return
        self._fail(
            "OX0101", f"expected end of statement, found {tok.kind.name}", tok.span
        )

    def _sync(self) -> None:
        """Skip tokens until a sync kind; consume it if it is a NEWLINE."""
        while self.tokens[self.pos].kind not in _SYNC_SET:
            self.pos += 1
        if self.tokens[self.pos].kind is TokenKind.NEWLINE:
            self.pos += 1

    def _recovered_span(self, start_tok: Token, start_idx: int) -> Span:
        if self.pos > start_idx:
            return Span(start_tok.span.start, self.tokens[self.pos - 1].span.end)
        return start_tok.span

    # ---- module & items ---------------------------------------------------

    def parse_module(self) -> Module:
        items: list[Item] = []
        while True:
            self._skip_newline_run()
            tok = self.tokens[self.pos]
            if tok.kind is TokenKind.EOF:
                break
            start_idx = self.pos
            self._skip_nl = False
            self._no_struct_lit = False
            try:
                items.append(self._item(tok))
            except _ParseError:
                self._skip_nl = False
                self._no_struct_lit = False
                self._sync()
                while self.tokens[self.pos].kind not in _ITEM_START:
                    self.pos += 1
                items.append(
                    ErrorStmt(self._new_id(), self._recovered_span(tok, start_idx))
                )
        end = self.tokens[-1].span.end
        return Module(self._new_id(), Span(0, end), tuple(items))

    def _item(self, tok: Token) -> Item:
        if tok.kind is TokenKind.KW_FN:
            return self._fn_decl()
        if tok.kind is TokenKind.KW_STRUCT:
            return self._struct_decl()
        if tok.kind is TokenKind.KW_ENUM:
            return self._enum_decl()
        self._fail(
            "OX0102", f"expected item at module level, found {tok.kind.name}", tok.span
        )
        raise AssertionError("unreachable")  # pragma: no cover

    def _fn_decl(self) -> FnDecl:
        kw = self._advance()
        name_tok = self._expect(TokenKind.IDENT, "function name")
        self._expect(TokenKind.LPAREN, "'('")
        saved = (self._skip_nl, self._no_struct_lit)
        self._skip_nl = True
        self._no_struct_lit = False
        params: list[Param] = []
        while not self._check(TokenKind.RPAREN):
            params.append(self._param())
            if not self._match(TokenKind.COMMA):
                break
        self._expect(TokenKind.RPAREN, "')'")
        self._skip_nl, self._no_struct_lit = saved
        ret_ty = self._type() if self._match(TokenKind.ARROW) else None
        # Function boundaries reset the loop depth (SPEC.md §34): a
        # break/continue directly in a fn body is OX0105 regardless of any
        # enclosing context the recovery machinery may have left behind.
        saved_depth = self._loop_depth
        self._loop_depth = 0
        try:
            body = self._block()
        finally:
            self._loop_depth = saved_depth
        span = Span(kw.span.start, body.span.end)
        return FnDecl(
            self._new_id(), span, name_tok.lexeme, tuple(params), ret_ty, body
        )

    def _param(self) -> Param:
        name_tok = self._expect(TokenKind.IDENT, "parameter name")
        ty = self._type() if self._match(TokenKind.COLON) else None
        end = ty.span.end if ty is not None else name_tok.span.end
        return Param(
            self._new_id(), Span(name_tok.span.start, end), name_tok.lexeme, ty
        )

    def _struct_decl(self) -> StructDecl:
        kw = self._advance()
        name_tok = self._expect(TokenKind.IDENT, "struct name")
        self._expect(TokenKind.LBRACE, "'{'")
        saved = self._skip_nl
        self._skip_nl = True
        fields: list[FieldDef] = []
        while not self._check(TokenKind.RBRACE):
            fields.append(self._field_def())
            if not self._match(TokenKind.COMMA):
                break
        rbrace = self._expect(TokenKind.RBRACE, "'}'")
        self._skip_nl = saved
        span = Span(kw.span.start, rbrace.span.end)
        return StructDecl(self._new_id(), span, name_tok.lexeme, tuple(fields))

    def _enum_decl(self) -> EnumDecl:
        kw = self._advance()
        name_tok = self._expect(TokenKind.IDENT, "enum name")
        self._expect(TokenKind.LBRACE, "'{'")
        saved = self._skip_nl
        self._skip_nl = True  # NEWLINEs skipped inside enum braces (SPEC.md §26)
        variants: list[tuple[str, tuple[TypeExpr, ...]]] = []
        while not self._check(TokenKind.RBRACE):
            variants.append(self._variant())
            if not self._match(TokenKind.COMMA):
                break
        rbrace = self._expect(TokenKind.RBRACE, "'}'")
        self._skip_nl = saved
        span = Span(kw.span.start, rbrace.span.end)
        return EnumDecl(self._new_id(), span, name_tok.lexeme, tuple(variants))

    def _variant(self) -> tuple[str, tuple[TypeExpr, ...]]:
        # NEWLINEs are skipped inside variant parens (SPEC.md §26): _skip_nl
        # is already True here, inherited from the enclosing enum braces.
        name_tok = self._expect(TokenKind.IDENT, "variant name")
        tys: list[TypeExpr] = []
        if self._match(TokenKind.LPAREN):
            # Grammar: "(" type ("," type)* ")" — at least one payload type,
            # no trailing comma inside the variant parens.
            tys.append(self._type())
            while self._match(TokenKind.COMMA):
                tys.append(self._type())
            self._expect(TokenKind.RPAREN, "')'")
        return (name_tok.lexeme, tuple(tys))

    def _field_def(self) -> FieldDef:
        name_tok = self._expect(TokenKind.IDENT, "field name")
        self._expect(TokenKind.COLON, "':'")
        ty = self._type()
        return FieldDef(
            self._new_id(),
            Span(name_tok.span.start, ty.span.end),
            name_tok.lexeme,
            ty,
        )

    def _type(self) -> TypeExpr:
        self._enter_nested()
        try:
            return self._type_inner()
        finally:
            self._depth -= 1

    def _type_inner(self) -> TypeExpr:
        tok = self._peek()
        if tok.kind is not TokenKind.IDENT:
            self._fail("OX0103", f"expected type, found {tok.kind.name}", tok.span)
        name_tok = self._advance()
        args: list[TypeExpr] = []
        end = name_tok.span.end
        if self._match(TokenKind.LT):
            saved = self._skip_nl
            self._skip_nl = True
            while True:
                args.append(self._type())
                if not self._match(TokenKind.COMMA):
                    break
            gt = self._expect(TokenKind.GT, "'>'")
            self._skip_nl = saved
            end = gt.span.end
        return TypeExpr(
            self._new_id(), Span(name_tok.span.start, end), name_tok.lexeme, tuple(args)
        )

    # ---- blocks & statements ----------------------------------------------

    def _block(self) -> Block:
        self._enter_nested()
        try:
            return self._block_inner()
        finally:
            self._depth -= 1

    def _block_inner(self) -> Block:
        # v0.2.1 newline tolerance (SPEC.md §34): a NEWLINE run is skipped
        # between a fn/if/while/for/match header and its `{`. Every _block
        # call site is such a header position (match-arm block bodies sit in
        # a NEWLINE-skipping context already), so the skip lives here.
        self._skip_newline_run()
        lbrace = self._expect(TokenKind.LBRACE, "'{'")
        saved = (self._skip_nl, self._no_struct_lit)
        self._skip_nl = False
        self._no_struct_lit = False
        stmts: list[Stmt] = []
        while True:
            self._skip_newline_run()
            if self.tokens[self.pos].kind in (TokenKind.RBRACE, TokenKind.EOF):
                break
            start_tok = self.tokens[self.pos]
            start_idx = self.pos
            try:
                stmts.append(self._statement())
            except _ParseError:
                self._skip_nl = False
                self._no_struct_lit = False
                self._sync()
                if self.pos == start_idx and self.tokens[self.pos].kind not in (
                    TokenKind.RBRACE,
                    TokenKind.EOF,
                ):
                    self.pos += 1
                stmts.append(
                    ErrorStmt(self._new_id(), self._recovered_span(start_tok, start_idx))
                )
        tail: Expr | None = None
        # while_stmt and for_stmt are distinct statement productions (SPEC.md
        # §§6, 26); they are wrapped in ExprStmt only for the dump form and
        # never tail-convert.
        if (
            stmts
            and isinstance(stmts[-1], ExprStmt)
            and not isinstance(stmts[-1].expr, (While, For))
            and self._check(TokenKind.RBRACE)
        ):
            tail = stmts.pop().expr
        rbrace = self._expect(TokenKind.RBRACE, "'}'")
        self._skip_nl, self._no_struct_lit = saved
        span = Span(lbrace.span.start, rbrace.span.end)
        return Block(self._new_id(), span, tuple(stmts), tail)

    def _statement(self) -> Stmt:
        kind = self._peek().kind
        if kind is TokenKind.KW_LET:
            return self._let_stmt()
        if kind is TokenKind.KW_RETURN:
            return self._return_stmt()
        if kind is TokenKind.KW_WHILE:
            return self._while_stmt()
        if kind is TokenKind.KW_FOR:
            return self._for_stmt()
        if kind is TokenKind.KW_BREAK:
            return self._break_stmt()
        if kind is TokenKind.KW_CONTINUE:
            return self._continue_stmt()
        # Field assignment (SPEC.md §56): IDENT (DOT IDENT)+ EQ. Tried
        # before the §26 IDENT-EQ branch because it starts the same way;
        # the scan restores the cursor and returns None when the run is
        # not an assignment, so `p.x` and `p.x == y` stay expressions.
        if kind is TokenKind.IDENT and self._peek_next().kind is TokenKind.DOT:
            field_assign = self._try_field_assign()
            if field_assign is not None:
                return field_assign
        # Assignment lookahead (SPEC.md §26): IDENT immediately followed by
        # EQ (EQEQ is a distinct token kind, so `x == y` stays a comparison).
        if kind is TokenKind.IDENT and self._peek_next().kind is TokenKind.EQ:
            return self._assign_stmt()
        return self._expr_stmt()

    def _skip_optional_mut(self) -> None:
        """Accept and ignore `mut` after `let` (SPEC.md §54).

        Oxide has no mutability distinction -- every binding is
        reassignable -- so `mut` carries no meaning and is discarded at
        the parser. It is a CONTEXTUAL keyword, not a reserved word: it is
        consumed only when an identifier follows, so `let mut = 1` still
        binds a variable named `mut`.

        Measured justification. Models write `let mut x` reflexively. Under
        grammar-constrained decoding this was worse than a plain error: GBNF
        cannot reject a token, only steer to the nearest valid string, so
        `let mut acc` was silently glued into `let mutacc` and every later
        use of `acc` became OX0200. That single artifact accounted for 44%
        of OX0200-carrying submissions across three model families -- the
        largest cause of the largest remaining error class.
        """
        nxt = self._peek()
        if (
            nxt.kind is TokenKind.IDENT
            and nxt.lexeme == "mut"
            and self._peek_next().kind is TokenKind.IDENT
        ):
            self._advance()

    def _let_stmt(self) -> Let:
        kw = self._advance()
        self._skip_optional_mut()
        pattern = self._pattern()
        ty = self._type() if self._match(TokenKind.COLON) else None
        self._expect(TokenKind.EQ, "'='")
        init = self._parse_expr(0)
        self._expect_term()
        span = Span(kw.span.start, init.span.end)
        return Let(self._new_id(), span, pattern, ty, init)

    def _pattern(self) -> Pattern:
        tok = self._peek()
        if tok.kind is not TokenKind.IDENT:
            self._fail("OX0104", f"expected pattern, found {tok.kind.name}", tok.span)
        name_tok = self._advance()
        if not self._check(TokenKind.LBRACE):
            return BindPat(self._new_id(), name_tok.span, name_tok.lexeme)
        self._advance()  # {
        # Pattern braces are not an enumerated NEWLINE-skip context (SPEC.md
        # §6), and the braced form requires at least one field name.
        names: list[str] = [self._expect(TokenKind.IDENT, "field name").lexeme]
        while self._match(TokenKind.COMMA):
            if self._check(TokenKind.RBRACE):
                break  # trailing comma
            names.append(self._expect(TokenKind.IDENT, "field name").lexeme)
        rbrace = self._expect(TokenKind.RBRACE, "'}'")
        span = Span(name_tok.span.start, rbrace.span.end)
        return DestructPat(self._new_id(), span, name_tok.lexeme, tuple(names))

    def _return_stmt(self) -> Return:
        kw = self._advance()
        value: Expr | None = None
        if self._peek().kind not in _TERM_LOOKAHEAD:
            value = self._parse_expr(0)
        self._expect_term()
        end = value.span.end if value is not None else kw.span.end
        return Return(self._new_id(), Span(kw.span.start, end), value)

    def _while_stmt(self) -> ExprStmt:
        kw = self._advance()
        saved = self._no_struct_lit
        self._no_struct_lit = True
        cond = self._parse_expr(0)
        self._no_struct_lit = saved
        self._loop_depth += 1
        try:
            body = self._block()
        finally:
            self._loop_depth -= 1
        span = Span(kw.span.start, body.span.end)
        node = While(self._new_id(), span, cond, body)
        return ExprStmt(self._new_id(), span, node)

    def _for_stmt(self) -> ExprStmt:
        kw = self._advance()
        var_tok = self._expect(TokenKind.IDENT, "loop variable")
        self._expect(TokenKind.KW_IN, "'in'")
        saved = self._no_struct_lit
        self._no_struct_lit = True  # §6 restriction extends to for iterables
        iterable = self._parse_expr(0)
        self._no_struct_lit = saved
        self._loop_depth += 1
        try:
            body = self._block()
        finally:
            self._loop_depth -= 1
        span = Span(kw.span.start, body.span.end)
        node = For(self._new_id(), span, var_tok.lexeme, iterable, body)
        return ExprStmt(self._new_id(), span, node)

    def _break_stmt(self) -> Break:
        kw = self._advance()
        if self._loop_depth == 0:
            # OX0105 (SPEC.md §34): break outside a while/for body. Reported
            # directly (no _ParseError): the statement itself is well-formed,
            # so parsing continues normally.
            self.diagnostics.append(
                Diagnostic("OX0105", "'break' outside a loop", kw.span)
            )
        self._expect_term()
        return Break(self._new_id(), kw.span)

    def _continue_stmt(self) -> Continue:
        kw = self._advance()
        if self._loop_depth == 0:
            self.diagnostics.append(
                Diagnostic("OX0105", "'continue' outside a loop", kw.span)
            )
        self._expect_term()
        return Continue(self._new_id(), kw.span)

    def _assign_stmt(self) -> Assign:
        name_tok = self._advance()  # IDENT (lookahead-verified by _statement)
        self._advance()  # EQ
        value = self._parse_expr(0)
        self._expect_term()
        span = Span(name_tok.span.start, value.span.end)
        return Assign(self._new_id(), span, name_tok.lexeme, value)

    def _try_field_assign(self) -> FieldAssign | None:
        """`a.b.c = e` (SPEC.md §56), or None with the cursor restored.

        Unbounded lookahead: the statement is a field assignment only if
        the whole `IDENT (DOT IDENT)+` run is followed by EQ. EQEQ is a
        distinct token kind, so `p.x == y` fails the scan and falls through
        to an expression statement.
        """
        start = self.pos
        base_tok = self._advance()  # IDENT (verified by _statement)
        path: list[str] = []
        while self._peek_raw().kind is TokenKind.DOT:
            self._advance()  # DOT
            if self._peek_raw().kind is not TokenKind.IDENT:
                self.pos = start
                return None
            path.append(self._advance().lexeme)
        if not path or self._peek_raw().kind is not TokenKind.EQ:
            self.pos = start
            return None
        self._advance()  # EQ
        value = self._parse_expr(0)
        self._expect_term()
        span = Span(base_tok.span.start, value.span.end)
        return FieldAssign(
            self._new_id(), span, base_tok.lexeme, tuple(path), value
        )

    def _expr_stmt(self) -> ExprStmt:
        start_idx = self.pos
        expr = self._parse_expr(0)
        if self.pos == start_idx:
            # nud failed without consuming anything; OX0100 already reported.
            raise _ParseError
        self._expect_term()
        return ExprStmt(self._new_id(), expr.span, expr)


def parse_source(source: str) -> tuple[Module, list[Diagnostic]]:
    """Lex + parse. Diagnostics = lexer's, then parser's. Never raises."""
    lexer = Lexer(source)
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    module = parser.parse_module()
    return module, [*lexer.diagnostics, *parser.diagnostics]
