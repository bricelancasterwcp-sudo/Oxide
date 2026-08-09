"""Rust code generation for Oxide (SPEC.md Parts IV-V, sections 21-24, 29).

``emit_rust`` renders a clean (diagnostic-free) ``SemaResult`` as a
complete Rust source file: the amended section-23/29 prelude followed by
the module's items in source order, with linearity realized through
explicit ``drop`` calls placed per the section-18/28 DropPoints and
read-mode borrows realized through the section-22 ref-form rules.
Language v0.2 (section 29) adds enum emission with the amended derives,
match emission (qualified user variants, bare Option/Result variants,
wildcard arms), ``for`` loops via ``ITER.iter().cloned()``, and
assignment with ``mut`` inference. Language v0.2.1 (section 37) adds
``break;``/``continue;`` with before-jump drops emitted immediately
before the jump, ``.clone()`` on non-copy field access, verbatim ``?``,
functional struct update, and the ``to_float``/``trunc`` prelude
additions. ``transpile`` is the analyze-then-emit convenience wrapper;
it never raises.
"""

from __future__ import annotations

from src.codegen.support import (
    BUILTIN_REF,
    CMP_PREC,
    EQ_OPS,
    HEADER,
    I32_MAX,
    INDENT,
    PREC,
    PRELUDE,
    emit_enum,
    emit_struct,
    eq_derive_types,
    escape,
    exposed_struct_lit,
    float_repr,
    iter_nodes,
    rust_string,
    rust_type,
    span_key,
    type_from_annotation,
    type_renames,
)
from src.diagnostics import Diagnostic
from src.parser import ast
from src.sema.analyze import SemaResult, analyze
from src.sema.linear import DropPoint
from src.sema.types import UNIT, is_copy

# ------------------------------------------------------------------ public API


def emit_rust(res: SemaResult) -> str:
    """Emit a full Rust file for a clean analysis result.

    Precondition: ``res.diagnostics == []``; raises ValueError otherwise.
    """
    if res.diagnostics:
        raise ValueError("emit_rust: SemaResult has diagnostics")
    renames = type_renames(res.module)
    eq_types = eq_derive_types(res)
    items: list[str] = []
    has_main = False
    for item in res.module.items:
        match item:
            case ast.StructDecl():
                items.append(emit_struct(item, renames, item.name in eq_types))
            case ast.EnumDecl():
                items.append(emit_enum(item, renames, item.name in eq_types))
            case ast.FnDecl():
                if item.name == "main":
                    has_main = True
                items.append(_FnEmitter(res, item, renames).emit())
    if not has_main:
        items.append("fn main() {}")
    return HEADER + "\n\n" + PRELUDE + "\n\n" + "\n\n".join(items) + "\n"


def transpile(source: str) -> tuple[str | None, list[Diagnostic]]:
    """Analyze + emit. ``(rust_text, [])`` on success, ``(None, diags)``
    otherwise. Never raises."""
    res = analyze(source)
    if res.diagnostics:
        return None, res.diagnostics
    return emit_rust(res), []


# -------------------------------------------------------------- reachability
#
# The analyses are reachability-aware: the linear checker stops at a
# diverging statement (_DIVERGED) and cfg._scan_moves collects no move
# evidence from code after a jump/return, so statements there carry no
# modes/drops. Codegen must mirror that model or the phases disagree on
# an analyze-clean program: emitting dead statements verbatim moves a
# ref-bound read-mode param by value (rustc E0308), and emitting
# block-end drops before a tail that can jump runs both the block-end
# drop and the jump's own before-jump/before-return drop (rustc E0382).


def _stmt_diverges(stmt: ast.Stmt) -> bool:
    """True iff the statement never falls through (mirrors
    cfg._scan_moves: break/continue/return, or an if/match whose arms
    all diverge; while/for never guarantee divergence)."""
    match stmt:
        case ast.Break() | ast.Continue() | ast.Return():
            return True
        case ast.ExprStmt(expr=expr):
            return _expr_diverges(expr)
    return False


def _expr_diverges(expr: ast.Expr) -> bool:
    match expr:
        case ast.If(then_blk=then_blk, else_blk=else_blk):
            if else_blk is None or not _block_diverges(then_blk):
                return False
            if isinstance(else_blk, ast.Block):
                return _block_diverges(else_blk)
            return _expr_diverges(else_blk)
        case ast.Match(arms=arms):
            return bool(arms) and all(
                _block_diverges(arm.body)
                if isinstance(arm.body, ast.Block)
                else _expr_diverges(arm.body)
                for arm in arms
            )
    return False


def _block_diverges(blk: ast.Block) -> bool:
    if any(_stmt_diverges(stmt) for stmt in blk.stmts):
        return True
    return blk.tail is not None and _expr_diverges(blk.tail)


def _may_diverge(expr: ast.Expr) -> bool:
    """Can evaluating ``expr`` jump out on SOME path (a break, continue,
    or return anywhere inside)? Block-end drops fire only on normal
    fall-through in the linear model (section 36 jump edges carry their
    own before-jump/before-return drops), so they must not be emitted
    before such a tail."""
    return any(
        isinstance(node, (ast.Break, ast.Continue, ast.Return))
        for node in iter_nodes(expr)
    )


# ------------------------------------------------------------------- functions


class _FnEmitter:
    """Emits one function: signature, body, and its DropPoints."""

    def __init__(
        self, res: SemaResult, fn: ast.FnDecl, renames: dict[str, str]
    ) -> None:
        self.res = res
        self.fn = fn
        self.type_renames = renames
        self.types = res.infer.types
        self.var_types = res.infer.var_types
        self.use_of = res.resolve.use_of
        self.binds_of = res.resolve.binds_of
        self.callee_of = res.resolve.callee_of
        self.variant_refs = res.resolve.variant_refs
        self.assign_of = res.resolve.assign_of
        # Var-ids ever assigned anywhere: their bindings are `mut`
        # (section 29). var_ids are module-unique, so the global set is
        # exact for this fn as well.
        self.assigned = frozenset(res.resolve.assign_of.values())
        # A user fn named `drop` would shadow std::mem::drop module-wide
        # and capture every synthesized drop call.
        self.drop_fn = (
            "std::mem::drop" if "drop" in res.resolve.fns else "drop"
        )
        self.rename = self._build_renames()
        self.ref_bound = self._build_ref_bound()
        # DropPoint indexes by anchor span, each list descending var_id.
        self.after_stmt: dict[tuple[int, int], list[DropPoint]] = {}
        self.block_end: dict[tuple[int, int], list[DropPoint]] = {}
        self.branch_end: dict[tuple[int, int], list[DropPoint]] = {}
        self.before_return: dict[tuple[int, int], list[DropPoint]] = {}
        self.before_jump: dict[tuple[int, int], list[DropPoint]] = {}
        self._index_drops()

    # ------------------------------------------------------------ fn context

    def _build_renames(self) -> dict[int, str]:
        """Shadow-numbered, keyword-escaped emitted name per var_id.

        Occupied names are skipped so a user identifier literally spelled
        ``name__2`` never collides with a generated rename; the reserved
        ``__oxide_ret`` (the synthesized tail/return temp) is pre-seeded
        as occupied."""
        counts: dict[str, int] = {}
        used: set[str] = {"__oxide_ret"}
        renames: dict[int, str] = {}
        for var_id in sorted(self.res.resolve.var_info):
            info = self.res.resolve.var_info[var_id]
            if info.fn != self.fn.name:
                continue
            nth = counts.get(info.name, 0) + 1
            candidate = escape(info.name) if nth == 1 else f"{info.name}__{nth}"
            while candidate in used:
                nth += 1
                candidate = f"{info.name}__{nth}"
            counts[info.name] = nth
            used.add(candidate)
            renames[var_id] = candidate
        return renames

    def _build_ref_bound(self) -> frozenset[int]:
        """Var-ids of this fn's read-mode non-copy params (ref-bound)."""
        modes = self.res.modes.modes.get(self.fn.name, ())
        out: set[int] = set()
        for index, param in enumerate(self.fn.params):
            if index >= len(modes) or modes[index] != "read":
                continue
            for var_id in self.binds_of.get(param.node_id, ()):
                if not is_copy(self.var_types.get(var_id, UNIT)):
                    out.add(var_id)
        return frozenset(out)

    def _index_drops(self) -> None:
        table = {
            "after-stmt": self.after_stmt,
            "block-end": self.block_end,
            "branch-end": self.branch_end,
            "before-return": self.before_return,
            "before-jump": self.before_jump,
        }
        for drop in self.res.linear.drops:
            if drop.fn != self.fn.name or drop.var_id < 0:
                continue  # <temp> drops are realized as drop(expr) statements
            table[drop.kind].setdefault(span_key(drop.anchor_span), []).append(drop)
        for index in table.values():
            for drop_list in index.values():
                drop_list.sort(key=lambda d: -d.var_id)

    def _drop_lines(self, drops: list[DropPoint], indent: int) -> list[str]:
        pad = INDENT * indent
        return [f"{pad}{self.drop_fn}({self.rename[d.var_id]});" for d in drops]

    def _ty(self, ty: object) -> str:
        return rust_type(ty, self.type_renames)

    def _type_name(self, name: str) -> str:
        return self.type_renames.get(name, escape(name))

    def _variant_text(self, vname: str) -> str:
        """Section 29: user variants qualify as ``Enum::Variant``;
        Option/Result variants emit bare (std prelude)."""
        owner = self.res.resolve.variants.get(vname)
        if owner is None:
            return vname
        return f"{self._type_name(owner)}::{escape(vname)}"

    def _mut(self, var_id: int) -> str:
        return "mut " if var_id in self.assigned else ""

    def _reads_any(self, expr: ast.Expr, var_ids: set[int]) -> bool:
        """Does ``expr`` mention any of these locals anywhere inside?"""
        return any(
            isinstance(node, ast.Var)
            and self.use_of.get(node.node_id) in var_ids
            for node in iter_nodes(expr)
        )

    # ------------------------------------------------------------- signature

    def emit(self) -> str:
        params: list[str] = []
        for param in self.fn.params:
            var_id = self.binds_of[param.node_id][0]
            name = self.rename[var_id]
            ty = self.var_types[var_id]
            amp = "&" if var_id in self.ref_bound else ""
            params.append(f"{self._mut(var_id)}{name}: {amp}{self._ty(ty)}")
        ret = self._ret_type()
        arrow = "" if ret == UNIT else f" -> {self._ty(ret)}"
        sig = f"fn {escape(self.fn.name)}({', '.join(params)}){arrow}"
        body = self._block_lines(self.fn.body, 1)
        if not body:
            return sig + " {}"
        return sig + " {\n" + "\n".join(body) + "\n}"

    def _ret_type(self) -> object:
        if self.fn.ret_ty is not None:
            return type_from_annotation(self.fn.ret_ty)
        body = self.fn.body
        if body.tail is not None:
            return self.types.get(body.tail.node_id, UNIT)
        if body.stmts and isinstance(body.stmts[-1], ast.Return):
            value = body.stmts[-1].value
            if value is not None:
                return self.types.get(value.node_id, UNIT)
        return UNIT

    # ----------------------------------------------------------------- blocks

    def _block_lines(self, blk: ast.Block, indent: int) -> list[str]:
        """Emit a block's statements, its anchored drops, and its tail."""
        lines: list[str] = []
        for stmt in blk.stmts:
            self._emit_stmt(stmt, indent, lines)
            drops = self.after_stmt.get(span_key(stmt.span))
            if drops:
                lines.extend(self._drop_lines(drops, indent))
            if _stmt_diverges(stmt):
                # Everything after this statement (including the tail and
                # any block-end drops) is unreachable. The analyses never
                # visited it, so emitting it verbatim would disagree with
                # inferred modes/drops (see the reachability note above).
                return lines
        end_drops = self.block_end.get(span_key(blk.span), [])
        if blk.tail is None:
            lines.extend(self._drop_lines(end_drops, indent))
            return lines
        if end_drops and (
            self._reads_any(blk.tail, {d.var_id for d in end_drops})
            or _may_diverge(blk.tail)
        ):
            # A branch-merge drop whose var the tail still reads, or a
            # tail that can jump (break/continue/return inside): compute
            # or emit the tail first, then run every pending drop — the
            # drops belong to the fall-through edge only; jump paths
            # already carry their own before-jump/before-return drops.
            tail_drops = self.after_stmt.get(span_key(blk.tail.span), [])
            self._value_then_drops(blk.tail, end_drops + tail_drops, indent, lines)
            return lines
        lines.extend(self._drop_lines(end_drops, indent))
        self._emit_tail(blk.tail, indent, lines)
        return lines

    def _emit_tail(self, tail: ast.Expr, indent: int, lines: list[str]) -> None:
        drops = self.after_stmt.get(span_key(tail.span))
        if not drops:
            lines.append(INDENT * indent + self._expr(tail, indent))
            return
        self._value_then_drops(tail, drops, indent, lines)

    def _value_then_drops(
        self,
        expr: ast.Expr,
        drops: list[DropPoint],
        indent: int,
        lines: list[str],
    ) -> None:
        """Value-position expression whose drops fire after evaluation:
        Unit-typed → emit it as a statement, then the drops; otherwise
        hoist into ``__oxide_ret`` (section 22, Drops)."""
        pad = INDENT * indent
        ty = self.types.get(expr.node_id, UNIT)
        if ty == UNIT:
            self._emit_discard(expr, indent, lines)
            lines.extend(self._drop_lines(drops, indent))
            return
        lines.append(
            f"{pad}let __oxide_ret: {self._ty(ty)} = {self._expr(expr, indent)};"
        )
        lines.extend(self._drop_lines(drops, indent))
        lines.append(pad + "__oxide_ret")

    # ------------------------------------------------------------- statements

    def _emit_stmt(self, stmt: ast.Stmt, indent: int, lines: list[str]) -> None:
        pad = INDENT * indent
        match stmt:
            case ast.Let():
                lines.append(pad + self._let_text(stmt, indent))
            case ast.Assign(value=value):
                var_id = self.assign_of[stmt.node_id]
                lines.append(
                    f"{pad}{self.rename[var_id]} = {self._expr(value, indent)};"
                )
            case ast.FieldAssign(path=path, value=value):
                # Section 56: a PLACE write. Deliberately not routed through
                # the FieldAccess emitter, which appends §36's `.clone()` to
                # a non-copy field value -- that would write into a
                # temporary and lose the assignment.
                var_id = self.assign_of[stmt.node_id]
                target = ".".join(
                    (self.rename[var_id], *(escape(f) for f in path))
                )
                lines.append(f"{pad}{target} = {self._expr(value, indent)};")
            case ast.Return(value=value):
                drops = self.before_return.get(span_key(stmt.span))
                if (
                    drops
                    and value is not None
                    and self._reads_any(value, {d.var_id for d in drops})
                ):
                    # The return value reads a var the before-return drops
                    # destroy: hoist it (same scheme as the value tail) so
                    # the read happens before the drops.
                    ret_ty = self._ty(self.types.get(value.node_id, UNIT))
                    lines.append(
                        f"{pad}let __oxide_ret: {ret_ty} = "
                        f"{self._expr(value, indent)};"
                    )
                    lines.extend(self._drop_lines(drops, indent))
                    lines.append(pad + "return __oxide_ret;")
                    return
                if drops:
                    lines.extend(self._drop_lines(drops, indent))
                if value is None:
                    lines.append(pad + "return;")
                else:
                    lines.append(f"{pad}return {self._expr(value, indent)};")
            case ast.Break() | ast.Continue():
                # Section 37: any before-jump drops (section 36) emit
                # immediately before the jump itself.
                drops = self.before_jump.get(span_key(stmt.span))
                if drops:
                    lines.extend(self._drop_lines(drops, indent))
                word = "break" if isinstance(stmt, ast.Break) else "continue"
                lines.append(f"{pad}{word};")
            case ast.ExprStmt(expr=expr):
                self._emit_discard(expr, indent, lines)

    def _let_text(self, stmt: ast.Let, indent: int) -> str:
        init = self._expr(stmt.init, indent)
        match stmt.pattern:
            case ast.BindPat() as pat:
                var_id = self.binds_of[pat.node_id][0]
                name = self.rename[var_id]
                ty = self._ty(self.var_types[var_id])
                return f"let {self._mut(var_id)}{name}: {ty} = {init};"
            case ast.DestructPat() as pat:
                var_ids = self.binds_of[pat.node_id]
                parts: list[str] = []
                for fname, var_id in zip(pat.field_names, var_ids):
                    field = escape(fname)
                    bound = self.rename[var_id]
                    mut = self._mut(var_id)
                    if field == bound:
                        parts.append(f"{mut}{field}" if mut else field)
                    else:
                        parts.append(f"{field}: {mut}{bound}")
                struct = self._type_name(pat.struct_name)
                return f"let {struct} {{ {', '.join(parts)} }} = {init};"
        raise ValueError("emit_rust: unknown pattern")

    def _emit_discard(self, expr: ast.Expr, indent: int, lines: list[str]) -> None:
        """Expression-statement position (also Unit-typed tails)."""
        pad = INDENT * indent
        if isinstance(expr, ast.While):
            lines.append(pad + self._while_text(expr, indent))
            return
        if isinstance(expr, ast.For):
            lines.append(pad + self._for_text(expr, indent))
            return
        ty = self.types.get(expr.node_id, UNIT)
        if isinstance(expr, (ast.If, ast.Match)):
            text = (
                self._if_text(expr, indent)
                if isinstance(expr, ast.If)
                else self._match_text(expr, indent)
            )
            if ty == UNIT:
                lines.append(pad + text)
            elif not is_copy(ty):
                lines.append(f"{pad}{self.drop_fn}({text});")
            else:
                # Section 22 pins `expr;` for copy-valued ExprStmts; a
                # block-form expression with a trailing `;` discards its value.
                lines.append(pad + text + ";")
            return
        if not is_copy(ty):
            lines.append(f"{pad}{self.drop_fn}({self._expr(expr, indent)});")
        else:
            lines.append(pad + self._expr(expr, indent) + ";")

    # ----------------------------------------------------------- control flow

    def _if_text(self, node: ast.If, indent: int) -> str:
        """An if/else chain; first line unindented, closing brace at
        ``indent``. Synthesizes ``else { drop(...); }`` for branch-end
        drops anchored at this If (section 22, Drops)."""
        pad = INDENT * indent
        cond = self._cond_text(node.cond, indent)
        then_lines = self._block_lines(node.then_blk, indent + 1)
        text = f"if {cond} {{\n" + "".join(f"{ln}\n" for ln in then_lines) + pad + "}"
        else_blk = node.else_blk
        if else_blk is None:
            drops = self.branch_end.get(span_key(node.span))
            if drops:
                body = "".join(f"{ln}\n" for ln in self._drop_lines(drops, indent + 1))
                text += " else {\n" + body + pad + "}"
            return text
        if isinstance(else_blk, ast.Block):
            else_lines = self._block_lines(else_blk, indent + 1)
            body = "".join(f"{ln}\n" for ln in else_lines)
            return text + " else {\n" + body + pad + "}"
        # Chained else-if. Hoisted block-end drops anchored at the chained
        # If (linear's synthetic block) force the nested-block form.
        hoisted = self.block_end.get(span_key(else_blk.span))
        if hoisted:
            inner_pad = INDENT * (indent + 1)
            inner: list[str] = [inner_pad + self._if_text(else_blk, indent + 1)]
            inner.extend(self._drop_lines(hoisted, indent + 1))
            body = "".join(f"{ln}\n" for ln in inner)
            return text + " else {\n" + body + pad + "}"
        return text + " else " + self._if_text(else_blk, indent)

    def _cond_text(self, cond: ast.Expr, indent: int) -> str:
        """An if/while condition or match scrutinee. Rust shares Oxide's
        no-struct-literal restriction here, but the AST has lost the
        source parentheses that licensed one — wrap the whole expression
        to relift it."""
        text = self._expr(cond, indent)
        if exposed_struct_lit(cond):
            return f"({text})"
        return text

    def _while_text(self, node: ast.While, indent: int) -> str:
        pad = INDENT * indent
        cond = self._cond_text(node.cond, indent)
        body_lines = self._block_lines(node.body, indent + 1)
        body = "".join(f"{ln}\n" for ln in body_lines)
        return f"while {cond} {{\n" + body + pad + "}"

    def _for_text(self, node: ast.For, indent: int) -> str:
        """Section 29: ``for VAR in ITER.iter().cloned() { … }`` — ITER
        bare for Var and Call expressions, parenthesized otherwise."""
        pad = INDENT * indent
        iter_text = self._expr(node.iterable, indent)
        if not isinstance(node.iterable, (ast.Var, ast.Call)):
            iter_text = f"({iter_text})"
        var_id = self.binds_of[node.node_id][0]
        loop_var = f"{self._mut(var_id)}{self.rename[var_id]}"
        body_lines = self._block_lines(node.body, indent + 1)
        body = "".join(f"{ln}\n" for ln in body_lines)
        return (
            f"for {loop_var} in {iter_text}.iter().cloned() {{\n" + body + pad + "}"
        )

    def _match_text(self, node: ast.Match, indent: int) -> str:
        """A match expression; first line unindented, closing brace at
        ``indent``. Expr-body arms emit ``PAT => EXPR,``; block-body arms
        ``PAT => { … }`` (section 29). Arm-anchored block-end drops
        (unconsumed binders, N-way merge hoisting per section 28) force
        the block form with the value computed before the drops."""
        pad = INDENT * indent
        arm_pad = INDENT * (indent + 1)
        scrut = self._cond_text(node.scrutinee, indent)
        lines = [f"match {scrut} {{"]
        for arm in node.arms:
            pat = self._pattern_text(arm.pattern)
            if isinstance(arm.body, ast.Block):
                body_lines = self._block_lines(arm.body, indent + 2)
                body = "".join(f"{ln}\n" for ln in body_lines)
                lines.append(f"{arm_pad}{pat} => {{\n{body}{arm_pad}}}")
                continue
            drops = self.block_end.get(span_key(arm.body.span))
            if drops:
                inner: list[str] = []
                self._value_then_drops(arm.body, drops, indent + 2, inner)
                body = "".join(f"{ln}\n" for ln in inner)
                lines.append(f"{arm_pad}{pat} => {{\n{body}{arm_pad}}}")
            else:
                lines.append(f"{arm_pad}{pat} => {self._expr(arm.body, indent + 1)},")
        lines.append(pad + "}")
        return "\n".join(lines)

    def _pattern_text(self, pat: ast.VariantPat) -> str:
        if pat.name is None:
            return "_"
        head = self._variant_text(pat.name)
        if not pat.binders:
            return head
        parts = [
            f"{self._mut(var_id)}{self.rename[var_id]}"
            for var_id in self.binds_of.get(pat.node_id, ())
        ]
        return f"{head}({', '.join(parts)})"

    # ------------------------------------------------------------ expressions

    def _expr(self, expr: ast.Expr, indent: int) -> str:
        match expr:
            case ast.Lit(value=value, kind=kind):
                return self._lit_text(value, kind)
            case ast.Var():
                return self._var_name(expr)
            case ast.Call():
                return self._call_text(expr, indent)
            case ast.BinOp():
                return self._binop_text(expr, indent)
            case ast.UnOp(op=op, operand=operand):
                inner = self._expr(operand, indent)
                if isinstance(operand, (ast.BinOp, ast.If, ast.Match)):
                    inner = f"({inner})"
                return op + inner
            case ast.FieldAccess(obj=obj, field=field):
                base = self._expr(obj, indent)
                if isinstance(obj, (ast.BinOp, ast.UnOp, ast.If, ast.Match)):
                    base = f"({base})"
                text = f"{base}.{escape(field)}"
                # Section 36: a non-copy field value is an implicit clone
                # (fresh owned value); copy fields emit unchanged.
                if not is_copy(self.types.get(expr.node_id, UNIT)):
                    text += ".clone()"
                return text
            case ast.StructLit(name=name, fields=fields, rest=rest):
                parts = [
                    f"{escape(fname)}: {self._expr(fexpr, indent)}"
                    for fname, fexpr in fields
                ]
                if rest is not None:
                    # Section 37: functional update emits identical Rust
                    # syntax; `..rest` is last.
                    rest_text = self._expr(rest, indent)
                    if isinstance(rest, (ast.If, ast.Match)):
                        rest_text = f"({rest_text})"
                    parts.append(f"..{rest_text}")
                if not parts:
                    return f"{self._type_name(name)} {{}}"
                return f"{self._type_name(name)} {{ {', '.join(parts)} }}"
            case ast.Try(operand=operand):
                # Section 37: `expr?` emits verbatim. `?` is postfix
                # (tightest tier) in both languages, so only looser-
                # binding operand forms need relifted parentheses.
                inner = self._expr(operand, indent)
                if isinstance(operand, (ast.BinOp, ast.UnOp, ast.If, ast.Match)):
                    inner = f"({inner})"
                return inner + "?"
            case ast.If():
                return self._if_text(expr, indent)
            case ast.Match():
                return self._match_text(expr, indent)
            case ast.While():
                return self._while_text(expr, indent)
            case ast.For():
                return self._for_text(expr, indent)
        raise ValueError(f"emit_rust: cannot emit expression {expr!r}")

    @staticmethod
    def _lit_text(value: object, kind: str) -> str:
        if kind == "bool":
            return "true" if value else "false"
        if kind == "float":
            return float_repr(value)  # type: ignore[arg-type]
        if kind == "str":
            return f'String::from("{rust_string(value)}")'  # type: ignore[arg-type]
        if kind == "int" and isinstance(value, int) and value > I32_MAX:
            # Unconstrained positions default the literal to i32 in Rust;
            # a value beyond i32 needs the explicit i64 suffix.
            return f"{value}i64"
        return str(value)

    def _var_name(self, expr: ast.Var) -> str:
        vname = self.variant_refs.get(expr.node_id)
        if vname is not None:
            return self._variant_text(vname)  # bare nullary variant value
        var_id = self.use_of.get(expr.node_id)
        if var_id is None:
            return escape(expr.name)
        return self.rename[var_id]

    # ----------------------------------------------------------------- calls

    def _call_text(self, call: ast.Call, indent: int) -> str:
        vname = self.variant_refs.get(call.node_id)
        if vname is not None:
            # Variant constructor: payloads are moves, always bare.
            args = ", ".join(self._expr(arg, indent) for arg in call.args)
            return f"{self._variant_text(vname)}({args})"
        name = self.callee_of.get(call.node_id)
        if name is None:  # unreachable in a clean module
            callee = self._expr(call.callee, indent)
        else:
            callee = escape(name)
        args_out: list[str] = []
        for index, arg in enumerate(call.args):
            if name is not None and self._ref_required(name, index):
                args_out.append(self._ref_form(arg, indent))
            else:
                args_out.append(self._expr(arg, indent))
        return f"{callee}({', '.join(args_out)})"

    def _ref_required(self, callee: str, index: int) -> bool:
        """Does this argument position require ref-form (section 22)?"""
        table = BUILTIN_REF.get(callee)
        if table is not None:
            return index < len(table) and table[index]
        modes = self.res.modes.modes.get(callee, ())
        if index >= len(modes) or modes[index] != "read":
            return False
        return not is_copy(self._callee_param_type(callee, index))

    def _callee_param_type(self, callee: str, index: int) -> object:
        fn_decl = self.res.resolve.fns.get(callee)
        if not isinstance(fn_decl, ast.FnDecl) or index >= len(fn_decl.params):
            return UNIT
        bound = self.binds_of.get(fn_decl.params[index].node_id, ())
        if not bound:
            return UNIT
        return self.var_types.get(bound[0], UNIT)

    def _ref_form(self, expr: ast.Expr, indent: int) -> str:
        """Ref-form(E): ref-bound Var -> name; owned Var -> &name;
        call or literal -> &E; anything else -> &(E)."""
        if isinstance(expr, ast.Var):
            var_id = self.use_of.get(expr.node_id)
            name = self._var_name(expr)
            if var_id is not None and var_id in self.ref_bound:
                return name
            return "&" + name
        text = self._expr(expr, indent)
        if isinstance(expr, (ast.Call, ast.Lit)):
            return "&" + text
        return f"&({text})"

    # ------------------------------------------------------------- operators

    def _binop_text(self, expr: ast.BinOp, indent: int) -> str:
        op = expr.op
        if op in EQ_OPS and not is_copy(self.types.get(expr.lhs.node_id, UNIT)):
            lhs = self._ref_form(expr.lhs, indent)
            rhs = self._ref_form(expr.rhs, indent)
            return f"{lhs} {op} {rhs}"
        lhs = self._operand_text(op, expr.lhs, False, indent)
        rhs = self._operand_text(op, expr.rhs, True, indent)
        return f"{lhs} {op} {rhs}"

    def _operand_text(
        self, parent_op: str, child: ast.Expr, is_right: bool, indent: int
    ) -> str:
        text = self._expr(child, indent)
        if isinstance(child, (ast.If, ast.Match)):
            # A block-form if/match at statement start would be parsed as
            # a statement, orphaning the trailing operator.
            return f"({text})"
        if isinstance(child, ast.BinOp):
            child_prec = PREC[child.op]
            parent_prec = PREC[parent_op]
            if child_prec < parent_prec or (
                child_prec == parent_prec
                # Rust's comparison tier is non-associative: an equal-
                # precedence comparison child needs parentheses on the
                # LEFT too, or rustc rejects the chained comparison.
                and (is_right or parent_prec == CMP_PREC)
            ):
                return f"({text})"
        return text
