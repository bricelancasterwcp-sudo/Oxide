"""Annotation stripping: dialect AST -> core AST + position records
(SPEC.md section 41).

Rebuilds the parsed dialect module into a pure core AST that is
structurally identical to what the core parser would produce on the
annotation-free source text, so the unchanged core analysis and codegen
run on it directly (byte-identical Rust). Node ids and spans of surviving
nodes are preserved. Three rewrites happen along the way:

- ``DropStmt`` nodes are removed from block statement lists; each is
  recorded as a :class:`WrittenDrop` carrying the set of core DropPoint
  anchors its written position can satisfy (kind + anchor span key, the
  same coordinates ``src.sema.linear`` uses).
- The core tail rule is re-applied where trailing drop statements
  followed the would-be tail expression (the fn-body tail counts as a
  statement position for after-stmt drops, so drops may be written after
  it; the core parser's tail conversion could not fire there).
- An ``else`` block consisting solely of drop statements strips to an
  ABSENT else — the dialect spelling of a branch-end drop ("via absent
  else"): its drops are recorded against the If node's span, where the
  core analysis of the stripped AST anchors the synthesized branch-end
  DropPoint.

The ``&`` records pass through untouched (they never live in the AST).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.diagnostics import Span
from src.explicit.parser import DropStmt
from src.parser import ast


def _key(span: Span) -> tuple[int, int]:
    return (span.start, span.end)


@dataclass(frozen=True, slots=True)
class WrittenDrop:
    """One written ``drop name`` statement, position-normalized."""

    fn: str
    name: str
    span: Span
    # (kind, anchor span key) pairs this written position satisfies.
    candidates: tuple[tuple[str, tuple[int, int]], ...]


@dataclass
class Annotations:
    """Everything the dialect wrote that core Oxide infers."""

    amp_uses: dict[int, Span]
    amp_params: dict[int, Span]
    drops: list[WrittenDrop] = field(default_factory=list)


def strip_module(
    module: ast.Module,
    amp_uses: dict[int, Span],
    amp_params: dict[int, Span],
) -> tuple[ast.Module, Annotations]:
    """Strip a dialect module to a core AST plus its annotation records."""
    stripper = _Stripper()
    stripped = stripper.module(module)
    return stripped, Annotations(dict(amp_uses), dict(amp_params), stripper.drops)


class _Stripper:
    def __init__(self) -> None:
        self.drops: list[WrittenDrop] = []
        self._fn: str = "?"

    # ---- items ------------------------------------------------------------

    def module(self, module: ast.Module) -> ast.Module:
        return replace(module, items=tuple(self._item(i) for i in module.items))

    def _item(self, item: ast.Item) -> ast.Item:
        if not isinstance(item, ast.FnDecl):
            return item
        self._fn = item.name
        body, top_drops = self._block(item.body)
        self.drops.extend(top_drops)
        return replace(item, body=body)

    # ---- blocks -----------------------------------------------------------

    def _block(self, blk: ast.Block) -> tuple[ast.Block, list[WrittenDrop]]:
        """Strip one block; returns it plus its own top-level drop records
        (nested blocks' records are appended to ``self.drops`` directly)."""
        parts: list[tuple[str, object]] = []
        for stmt in blk.stmts:
            if isinstance(stmt, DropStmt):
                parts.append(("drop", stmt))
            else:
                parts.append(("stmt", self._stmt(stmt)))
        tail = self._expr(blk.tail) if blk.tail is not None else None
        tail_index = -1
        if tail is None:
            # Re-apply the core tail rule across a trailing drop run: the
            # drops prevented the parser's own conversion from firing.
            k = len(parts)
            while k > 0 and parts[k - 1][0] == "drop":
                k -= 1
            if k < len(parts) and k > 0 and parts[k - 1][0] == "stmt":
                last = parts[k - 1][1]
                if isinstance(last, ast.ExprStmt) and not isinstance(
                    last.expr, (ast.While, ast.For)
                ):
                    tail = last.expr
                    tail_index = k - 1
        drops = [
            self._drop_record(blk, parts, index, tail, tail_index)
            for index, (kind, _node) in enumerate(parts)
            if kind == "drop"
        ]
        new_stmts = tuple(
            node
            for index, (kind, node) in enumerate(parts)
            if kind == "stmt" and index != tail_index
        )
        return replace(blk, stmts=new_stmts, tail=tail), drops  # type: ignore[arg-type]

    def _drop_record(
        self,
        blk: ast.Block,
        parts: list[tuple[str, object]],
        index: int,
        tail: ast.Expr | None,
        tail_index: int,
    ) -> WrittenDrop:
        """Anchor candidates for the drop written at ``parts[index]``.

        Both sides of the later diff use the SAME coordinates the core
        checker does (SPEC.md section 18 / src.sema.cfg): after-stmt
        anchors at the preceding statement's span (a tail expression is a
        statement position), block-end at the block's span (codegen puts
        those drops at the end of the statements region, before any
        tail), and before-return/before-jump at the immediately following
        return/break/continue statement's span.
        """
        drop = parts[index][1]
        assert isinstance(drop, DropStmt)
        prev_stmt: ast.Stmt | None = None
        post_tail = False
        for m in range(index - 1, -1, -1):
            if parts[m][0] == "stmt":
                if m == tail_index:
                    post_tail = True
                else:
                    prev_stmt = parts[m][1]  # type: ignore[assignment]
                break
        next_stmt: ast.Stmt | None = None
        for m in range(index + 1, len(parts)):
            if parts[m][0] == "stmt" and m != tail_index:
                next_stmt = parts[m][1]  # type: ignore[assignment]
                break
        cands: list[tuple[str, tuple[int, int]]] = []
        if post_tail and tail is not None:
            cands.append(("after-stmt", _key(tail.span)))
            cands.append(("block-end", _key(blk.span)))
        elif prev_stmt is not None:
            cands.append(("after-stmt", _key(prev_stmt.span)))
        if next_stmt is not None:
            if isinstance(next_stmt, ast.Return):
                cands.append(("before-return", _key(next_stmt.span)))
            elif isinstance(next_stmt, (ast.Break, ast.Continue)):
                cands.append(("before-jump", _key(next_stmt.span)))
        elif not post_tail:
            # Trailing run: the end of the block's statements region.
            cands.append(("block-end", _key(blk.span)))
        return WrittenDrop(self._fn, drop.name, drop.span, tuple(cands))

    # ---- statements -------------------------------------------------------

    def _stmt(self, stmt: ast.Stmt) -> ast.Stmt:
        match stmt:
            case ast.Let(init=init):
                return replace(stmt, init=self._expr(init))
            case ast.Assign(value=value):
                return replace(stmt, value=self._expr(value))
            case ast.Return(value=value):
                if value is None:
                    return stmt
                return replace(stmt, value=self._expr(value))
            case ast.ExprStmt(expr=expr):
                return replace(stmt, expr=self._expr(expr))
            case _:  # Break / Continue / ErrorStmt
                return stmt

    # ---- expressions ------------------------------------------------------

    def _expr(self, expr: ast.Expr) -> ast.Expr:
        match expr:
            case ast.If():
                return self._if(expr)
            case ast.Match(scrutinee=scrutinee, arms=arms):
                return replace(
                    expr,
                    scrutinee=self._expr(scrutinee),
                    arms=tuple(self._arm(arm) for arm in arms),
                )
            case ast.While(cond=cond, body=body):
                blk, drops = self._block(body)
                self.drops.extend(drops)
                return replace(expr, cond=self._expr(cond), body=blk)
            case ast.For(iterable=iterable, body=body):
                blk, drops = self._block(body)
                self.drops.extend(drops)
                return replace(expr, iterable=self._expr(iterable), body=blk)
            case ast.Call(callee=callee, args=args):
                return replace(
                    expr,
                    callee=self._expr(callee),
                    args=tuple(self._expr(a) for a in args),
                )
            case ast.BinOp(lhs=lhs, rhs=rhs):
                return replace(expr, lhs=self._expr(lhs), rhs=self._expr(rhs))
            case ast.UnOp(operand=operand):
                return replace(expr, operand=self._expr(operand))
            case ast.FieldAccess(obj=obj):
                return replace(expr, obj=self._expr(obj))
            case ast.Try(operand=operand):
                return replace(expr, operand=self._expr(operand))
            case ast.StructLit(fields=fields, rest=rest):
                return replace(
                    expr,
                    fields=tuple((n, self._expr(e)) for n, e in fields),
                    rest=self._expr(rest) if rest is not None else None,
                )
            case _:  # Var / Lit / ErrorExpr
                return expr

    def _arm(self, arm: ast.MatchArm) -> ast.MatchArm:
        if isinstance(arm.body, ast.Block):
            blk, drops = self._block(arm.body)
            self.drops.extend(drops)
            return replace(arm, body=blk)
        return replace(arm, body=self._expr(arm.body))

    def _if(self, expr: ast.If) -> ast.If:
        cond = self._expr(expr.cond)
        then_blk, then_drops = self._block(expr.then_blk)
        self.drops.extend(then_drops)
        else_blk: ast.Block | ast.If | None = None
        match expr.else_blk:
            case ast.Block() as blk:
                stripped, else_drops = self._block(blk)
                if (
                    not stripped.stmts
                    and stripped.tail is None
                    and else_drops
                ):
                    # A drops-only else is the dialect spelling of the
                    # branch-end drop: strip to an ABSENT else and anchor
                    # the written drops at the If (section 18 branch-end).
                    for drop in else_drops:
                        self.drops.append(
                            WrittenDrop(
                                drop.fn,
                                drop.name,
                                drop.span,
                                (("branch-end", _key(expr.span)),),
                            )
                        )
                else:
                    self.drops.extend(else_drops)
                    else_blk = stripped
            case ast.If() as chained:
                else_blk = self._if(chained)
        return replace(expr, cond=cond, then_blk=then_blk, else_blk=else_blk)
