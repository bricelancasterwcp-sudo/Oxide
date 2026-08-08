"""Whole-program monomorphic Hindley-Milner inference (SPEC.md
sections 14-16, 28, 36).

Every user fn signature starts as metavariables; all bodies and call
sites constrain them; one global union-find solve. Builtins are the only
polymorphic functions and are instantiated fresh per use (v0.2 adds the
builtin generic enums Option/Result, whose variants instantiate the same
way). After the solve: operator operand checks (OX0305), deferred match
shape checks (OX0307), ambiguity checks (OX0302), and finalization (any
type still containing an unsolved metavariable becomes ERROR_TYPE).
ERROR_TYPE unifies with everything and poisons the metavariables it
touches, suppressing downstream diagnostics.

Enum/variant/match typing lives in :mod:`src.sema.enums` (mixin).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.diagnostics import Diagnostic, Span
from src.parser import ast
from src.sema.enums import _EnumOps
from src.sema.resolve import ResolveResult
from src.sema.types import (
    BOOL,
    BUILTIN_ENUMS,
    BUILTINS,
    ERROR_TYPE,
    FLOAT,
    INT,
    STR,
    TCon,
    TFn,
    TVar,
    Type,
    UNIT,
    type_str,
)

_PRIMITIVE_ARITY: dict[str, int] = {
    "Int": 0,
    "Float": 0,
    "Bool": 0,
    "Str": 0,
    "Unit": 0,
    "Vec": 1,
}
_NUMERIC_NAMES = frozenset({"Int", "Float"})
_ERROR_NAME = ERROR_TYPE.name
_LIT_TYPES: dict[str, TCon] = {"int": INT, "float": FLOAT, "str": STR, "bool": BOOL}
_ARITH_OPS = frozenset({"+", "-", "*", "/"})
_I64_MAX = 2**63 - 1
_ORD_OPS = frozenset({"<", "<=", ">", ">="})
_EQ_OPS = frozenset({"==", "!="})
_BOOL_OPS = frozenset({"&&", "||"})


@dataclass
class InferResult:
    """Output of :func:`infer` (SPEC.md section 15)."""

    types: dict[int, Type] = field(default_factory=dict)
    var_types: dict[int, Type] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)


def infer(module: ast.Module, resolved: ResolveResult) -> InferResult:
    """Type-check a resolved module. Never raises."""
    return _Infer(module, resolved).run()


class _Infer(_EnumOps):
    def __init__(self, module: ast.Module, resolved: ResolveResult) -> None:
        self.module = module
        self.resolved = resolved
        self.diagnostics: list[Diagnostic] = []
        self._next_tvar = 0
        self._subst: dict[int, Type] = {}
        # var_id -> metavariable (created up-front, in var_id order)
        self.var_tv: dict[int, Type] = {
            var_id: self._fresh() for var_id in sorted(resolved.var_info)
        }
        self.struct_fields: dict[str, dict[str, Type]] = {}
        # user enum name -> {variant name -> payload types}
        self.enum_variants: dict[str, dict[str, tuple[Type, ...]]] = {}
        self.fn_sigs: dict[str, tuple[tuple[Type, ...], Type]] = {}
        self._cur_ret: Type = UNIT
        # expr/block node_id -> (unfinalized) type, in traversal order
        self.node_types: dict[int, Type] = {}
        self.node_spans: dict[int, Span] = {}
        # deferred checks
        self._op_checks: list[tuple[str, Type, Span, str]] = []
        self._pending_fields: list[tuple[Type, str, Span, Type]] = []
        self._pending_matches: list[tuple[Type, Span]] = []
        # (operand ty, enclosing fn return ty, span, result tv) per '?'
        self._pending_tries: list[tuple[Type, Type, Span, Type]] = []

    # ------------------------------------------------------------ plumbing

    def _diag(self, code: str, message: str, span: Span) -> None:
        self.diagnostics.append(Diagnostic(code, message, span))

    def _fresh(self) -> TVar:
        tv = TVar(self._next_tvar)
        self._next_tvar += 1
        return tv

    def _prune(self, ty: Type) -> Type:
        """Follow metavariable bindings to the representative (1 level deep
        in structure, full depth in the chain), with path compression."""
        seen: list[int] = []
        while isinstance(ty, TVar) and ty.id in self._subst:
            seen.append(ty.id)
            ty = self._subst[ty.id]
        for tv_id in seen[:-1]:
            self._subst[tv_id] = ty
        return ty

    def _resolve_full(self, ty: Type) -> Type:
        ty = self._prune(ty)
        match ty:
            case TCon(name=name, args=args) if args:
                return TCon(name, tuple(self._resolve_full(a) for a in args))
            case TFn(params=params, ret=ret):
                return TFn(
                    tuple(self._resolve_full(p) for p in params),
                    self._resolve_full(ret),
                )
            case _:
                return ty

    def _unsolved_roots(self, ty: Type) -> set[int]:
        ty = self._prune(ty)
        match ty:
            case TVar(id=tv_id):
                return {tv_id}
            case TCon(args=args):
                roots: set[int] = set()
                for arg in args:
                    roots |= self._unsolved_roots(arg)
                return roots
            case TFn(params=params, ret=ret):
                roots = self._unsolved_roots(ret)
                for param in params:
                    roots |= self._unsolved_roots(param)
                return roots
        return set()

    def _poison(self, ty: Type) -> None:
        """Bind every unsolved metavariable inside ``ty`` to ERROR_TYPE so
        one reported error does not cascade."""
        for tv_id in self._unsolved_roots(ty):
            self._subst[tv_id] = ERROR_TYPE

    def _occurs(self, tv_id: int, ty: Type) -> bool:
        return tv_id in self._unsolved_roots(ty)

    # ----------------------------------------------------------- unification

    def unify(self, a: Type, b: Type, span: Span) -> bool:
        a = self._prune(a)
        b = self._prune(b)
        if isinstance(a, TVar) and isinstance(b, TVar) and a.id == b.id:
            return True
        if isinstance(a, TVar):
            return self._bind(a, b, span)
        if isinstance(b, TVar):
            return self._bind(b, a, span)
        if isinstance(a, TCon) and a.name == _ERROR_NAME:
            self._poison(b)
            return True
        if isinstance(b, TCon) and b.name == _ERROR_NAME:
            self._poison(a)
            return True
        if isinstance(a, TCon) and isinstance(b, TCon):
            if a.name != b.name or len(a.args) != len(b.args):
                self._mismatch(a, b, span)
                return False
            ok = True
            for x, y in zip(a.args, b.args):
                if not self.unify(x, y, span):
                    ok = False
            return ok
        if isinstance(a, TFn) and isinstance(b, TFn):
            if len(a.params) != len(b.params):
                self._mismatch(a, b, span)
                return False
            ok = self.unify(a.ret, b.ret, span)
            for x, y in zip(a.params, b.params):
                if not self.unify(x, y, span):
                    ok = False
            return ok
        self._mismatch(a, b, span)
        return False

    def _bind(self, tv: TVar, ty: Type, span: Span) -> bool:
        if not isinstance(ty, TVar) and self._occurs(tv.id, ty):
            self._diag(
                "OX0301",
                f"infinite type: ? occurs in {type_str(self._resolve_full(ty))}",
                span,
            )
            self._subst[tv.id] = ERROR_TYPE
            self._poison(ty)
            return False
        self._subst[tv.id] = ty
        return True

    def _mismatch(self, a: Type, b: Type, span: Span) -> None:
        left = type_str(self._resolve_full(a))
        right = type_str(self._resolve_full(b))
        self._diag("OX0300", f"type mismatch: {left} vs {right}", span)
        self._poison(a)
        self._poison(b)

    def _instantiate(self, ty: Type, mapping: dict[int, Type]) -> Type:
        match ty:
            case TVar(id=tv_id):
                return mapping.get(tv_id, ty)
            case TCon(name=name, args=args) if args:
                return TCon(name, tuple(self._instantiate(a, mapping) for a in args))
            case _:
                return ty

    # ------------------------------------------------------- type annotations

    def _convert(self, ty_expr: ast.TypeExpr) -> Type:
        args = tuple(self._convert(a) for a in ty_expr.args)
        name = ty_expr.name
        if name in _PRIMITIVE_ARITY:
            if len(args) != _PRIMITIVE_ARITY[name]:
                self._diag(
                    "OX0202",
                    f"wrong number of type arguments for '{name}'",
                    ty_expr.span,
                )
                return ERROR_TYPE
            return TCon(name, args)
        if name in BUILTIN_ENUMS:
            if len(args) != len(BUILTIN_ENUMS[name].generics):
                self._diag(
                    "OX0202",
                    f"wrong number of type arguments for '{name}'",
                    ty_expr.span,
                )
                return ERROR_TYPE
            return TCon(name, args)
        if name in self.resolved.structs:
            if args:
                self._diag(
                    "OX0202",
                    f"struct '{name}' takes no type arguments",
                    ty_expr.span,
                )
                return ERROR_TYPE
            return TCon(name)
        if name in self.resolved.enums:
            if args:
                self._diag(
                    "OX0202",
                    f"enum '{name}' takes no type arguments",
                    ty_expr.span,
                )
                return ERROR_TYPE
            return TCon(name)
        self._diag("OX0202", f"unknown type name '{name}'", ty_expr.span)
        return ERROR_TYPE

    # ------------------------------------------------------------- top level

    def run(self) -> InferResult:
        self._declare_headers()
        for item in self.module.items:
            if isinstance(item, ast.FnDecl) and (
                self.resolved.fns.get(item.name) is item
            ):
                self._check_fn(item)
        self._flush_deferred()
        if self._default_unsolved_returns():
            self._flush_deferred()
        self._poison_leftover_deferred()
        self._check_pending_matches()
        self._run_op_checks()
        self._check_main()
        return self._finalize()

    def _declare_headers(self) -> None:
        for item in self.module.items:
            match item:
                case ast.StructDecl() if (
                    self.resolved.structs.get(item.name) is item
                ):
                    fmap: dict[str, Type] = {}
                    for field_def in item.fields:
                        fmap[field_def.name] = self._convert(field_def.ty)
                    self.struct_fields[item.name] = fmap
                case ast.EnumDecl() if self.resolved.enums.get(item.name) is item:
                    vmap: dict[str, tuple[Type, ...]] = {}
                    for vname, payload_tys in item.variants:
                        vmap[vname] = tuple(
                            self._convert(t) for t in payload_tys
                        )
                    self.enum_variants[item.name] = vmap
                case ast.FnDecl() if self.resolved.fns.get(item.name) is item:
                    param_tys: list[Type] = []
                    for param in item.params:
                        bound = self.resolved.binds_of.get(param.node_id, ())
                        tv: Type = self.var_tv[bound[0]] if bound else self._fresh()
                        if param.ty is not None:
                            self.unify(tv, self._convert(param.ty), param.span)
                        param_tys.append(tv)
                    ret_tv: Type = self._fresh()
                    if item.ret_ty is not None:
                        self.unify(
                            ret_tv, self._convert(item.ret_ty), item.ret_ty.span
                        )
                    self.fn_sigs[item.name] = (tuple(param_tys), ret_tv)

    def _check_fn(self, fn: ast.FnDecl) -> None:
        self._cur_ret = self.fn_sigs[fn.name][1]
        body_ty = self._block(fn.body)
        terminated = (
            fn.body.tail is None
            and bool(fn.body.stmts)
            and isinstance(fn.body.stmts[-1], ast.Return)
        )
        if not terminated:
            span = fn.body.tail.span if fn.body.tail is not None else fn.body.span
            self.unify(body_ty, self._cur_ret, span)

    def _default_unsolved_returns(self) -> bool:
        """A fn whose return is still a bare metavariable after the solve
        (e.g. pure recursion) returns Unit — unless that metavariable is
        shared with a param/binder, which must stay unsolved so the
        binder reports OX0302 instead of a fabricated Unit."""
        binder_roots: set[int] = set()
        for tv in self.var_tv.values():
            binder_roots |= self._unsolved_roots(tv)
        changed = False
        for _params, ret in self.fn_sigs.values():
            pruned = self._prune(ret)
            if isinstance(pruned, TVar) and pruned.id not in binder_roots:
                self._subst[pruned.id] = UNIT
                changed = True
        return changed

    # ------------------------------------------------------ blocks and stmts

    def _record(self, node: ast.Expr | ast.Block, ty: Type) -> Type:
        self.node_types[node.node_id] = ty
        self.node_spans[node.node_id] = node.span
        return ty

    def _block(self, block: ast.Block) -> Type:
        for stmt in block.stmts:
            self._stmt(stmt)
        ty: Type = UNIT
        if block.tail is not None:
            ty = self._expr(block.tail)
        return self._record(block, ty)

    def _stmt(self, stmt: ast.Stmt) -> None:
        match stmt:
            case ast.Let():
                self._let(stmt)
            case ast.Assign(value=value):
                # Section 28: the value unifies with the target's type.
                value_ty = self._expr(value)
                var_id = self.resolved.assign_of.get(stmt.node_id)
                if var_id is not None:
                    self.unify(value_ty, self.var_tv[var_id], value.span)
            case ast.Return(value=value):
                val_ty = self._expr(value) if value is not None else UNIT
                self.unify(val_ty, self._cur_ret, stmt.span)
            case ast.Break() | ast.Continue():
                # Section 36: pure control flow — no typing constraints.
                pass
            case ast.ExprStmt(expr=expr):
                self._expr(expr)
            case ast.ErrorStmt():
                pass

    def _let(self, stmt: ast.Let) -> None:
        init_ty = self._expr(stmt.init)
        target = init_ty
        if stmt.ty is not None:
            annot = self._convert(stmt.ty)
            self.unify(init_ty, annot, stmt.init.span)
            target = annot
        match stmt.pattern:
            case ast.BindPat() as pat:
                bound = self.resolved.binds_of.get(pat.node_id, ())
                if bound:
                    self.unify(self.var_tv[bound[0]], target, pat.span)
            case ast.DestructPat() as pat:
                self._destructure(pat, target, stmt.init.span)

    def _destructure(
        self, pat: ast.DestructPat, scrutinee: Type, init_span: Span
    ) -> None:
        var_ids = self.resolved.binds_of.get(pat.node_id, ())
        if pat.struct_name not in self.struct_fields:
            self._diag(
                "OX0202", f"unknown struct '{pat.struct_name}'", pat.span
            )
            self.unify(scrutinee, ERROR_TYPE, init_span)
            for var_id in var_ids:
                self.unify(self.var_tv[var_id], ERROR_TYPE, pat.span)
            return
        self.unify(scrutinee, TCon(pat.struct_name), init_span)
        fmap = self.struct_fields[pat.struct_name]
        seen: set[str] = set()
        for fname, var_id in zip(pat.field_names, var_ids):
            if fname not in fmap:
                self._diag(
                    "OX0304",
                    f"struct '{pat.struct_name}' has no field '{fname}'",
                    pat.span,
                )
                self.unify(self.var_tv[var_id], ERROR_TYPE, pat.span)
                continue
            seen.add(fname)
            self.unify(self.var_tv[var_id], fmap[fname], pat.span)
        missing = [f for f in fmap if f not in seen]
        if missing:
            self._diag(
                "OX0304",
                "incomplete destructure: missing "
                + ", ".join(f"'{f}'" for f in missing),
                pat.span,
            )

    # ---------------------------------------------------------- expressions

    def _expr(self, expr: ast.Expr) -> Type:
        match expr:
            case ast.Lit(kind=kind):
                ty: Type = _LIT_TYPES[kind]
                # Int is a 64-bit type; a literal beyond i64 cannot be
                # represented (and could never be emitted as Rust).
                if kind == "int" and expr.value > _I64_MAX:  # type: ignore[operator]
                    self._diag(
                        "OX0300",
                        "integer literal out of range for Int "
                        f"(max {_I64_MAX})",
                        expr.span,
                    )
            case ast.Var():
                var_id = self.resolved.use_of.get(expr.node_id)
                if var_id is not None:
                    ty = self.var_tv[var_id]
                else:
                    vname = self.resolved.variant_refs.get(expr.node_id)
                    ty = (
                        self._bare_variant(expr, vname)
                        if vname is not None
                        else ERROR_TYPE
                    )
            case ast.Call():
                ty = self._call(expr)
            case ast.BinOp():
                ty = self._binop(expr)
            case ast.UnOp():
                ty = self._unop(expr)
            case ast.FieldAccess():
                ty = self._field_access(expr)
            case ast.Try():
                ty = self._try_expr(expr)
            case ast.StructLit():
                ty = self._struct_lit(expr)
            case ast.If():
                ty = self._if(expr)
            case ast.While(cond=cond, body=body):
                cond_ty = self._expr(cond)
                self.unify(cond_ty, BOOL, cond.span)
                body_ty = self._block(body)
                # Loop body blocks must be Unit (like the absent-else rule
                # in section 14): Rust requires a loop body block to be
                # (), so a non-Unit tail would emit Rust failing E0308.
                if body.tail is not None:
                    self.unify(body_ty, UNIT, body.tail.span)
                ty = UNIT
            case ast.For():
                ty = self._for(expr)
            case ast.Match():
                ty = self._match_expr(expr)
            case ast.ErrorExpr():
                ty = ERROR_TYPE
            case _:
                ty = ERROR_TYPE
        return self._record(expr, ty)

    def _for(self, expr: ast.For) -> Type:
        """Section 28: the iterable must solve to Vec<T>; the loop variable
        is an owned clone of the element, scoped to the body."""
        iter_ty = self._expr(expr.iterable)
        elem = self._fresh()
        self.unify(iter_ty, TCon("Vec", (elem,)), expr.iterable.span)
        bound = self.resolved.binds_of.get(expr.node_id, ())
        if bound:
            self.unify(self.var_tv[bound[0]], elem, expr.span)
        body_ty = self._block(expr.body)
        # Same Unit constraint as while bodies: Rust loop body blocks
        # are (), so a non-Unit tail would emit Rust failing E0308.
        if expr.body.tail is not None:
            self.unify(body_ty, UNIT, expr.body.tail.span)
        return UNIT

    def _call(self, call: ast.Call) -> Type:
        vname = self.resolved.variant_refs.get(call.node_id)
        if vname is not None:
            return self._variant_call(call, vname)
        name = self.resolved.callee_of.get(call.node_id)
        if name is None:
            callee_ty = self._expr(call.callee)
            for arg in call.args:
                self._expr(arg)
            pruned = self._prune(callee_ty)
            if not (isinstance(pruned, TCon) and pruned.name == _ERROR_NAME):
                self._diag(
                    "OX0303",
                    f"not callable: {type_str(self._resolve_full(pruned))}",
                    call.callee.span,
                )
            return ERROR_TYPE
        if name in self.fn_sigs:
            params, ret = self.fn_sigs[name]
        else:
            sig = BUILTINS[name]
            mapping = {g.id: self._fresh() for g in sig.generics}
            params = tuple(self._instantiate(p, mapping) for p in sig.params)
            ret = self._instantiate(sig.ret, mapping)
        if len(call.args) != len(params):
            self._diag(
                "OX0303",
                f"'{name}' expects {len(params)} argument(s), found "
                f"{len(call.args)}",
                call.span,
            )
            for arg in call.args:
                self._expr(arg)
            return ERROR_TYPE
        for arg, param_ty in zip(call.args, params):
            arg_ty = self._expr(arg)
            self.unify(arg_ty, param_ty, arg.span)
        return ret

    def _binop(self, expr: ast.BinOp) -> Type:
        lhs_ty = self._expr(expr.lhs)
        rhs_ty = self._expr(expr.rhs)
        op = expr.op
        # The post-solve operand check is enqueued only when the operands
        # unified: a failed unification already reported OX0300 for this
        # node, and stacking OX0305 on top would double-report it.
        if op in _ARITH_OPS:
            if self.unify(lhs_ty, rhs_ty, expr.span):
                self._op_checks.append(("num", lhs_ty, expr.span, op))
            return lhs_ty
        if op == "%":
            if self.unify(lhs_ty, rhs_ty, expr.span):
                self._op_checks.append(("int", lhs_ty, expr.span, op))
            return lhs_ty
        if op in _ORD_OPS:
            if self.unify(lhs_ty, rhs_ty, expr.span):
                self._op_checks.append(("num", lhs_ty, expr.span, op))
            return BOOL
        if op in _EQ_OPS:
            self.unify(lhs_ty, rhs_ty, expr.span)
            return BOOL
        if op in _BOOL_OPS:
            self.unify(lhs_ty, BOOL, expr.lhs.span)
            self.unify(rhs_ty, BOOL, expr.rhs.span)
            return BOOL
        return ERROR_TYPE

    def _unop(self, expr: ast.UnOp) -> Type:
        operand_ty = self._expr(expr.operand)
        if expr.op == "!":
            self.unify(operand_ty, BOOL, expr.operand.span)
            return BOOL
        # unary '-'
        self._op_checks.append(("num", operand_ty, expr.span, expr.op))
        return operand_ty

    def _if(self, expr: ast.If) -> Type:
        cond_ty = self._expr(expr.cond)
        self.unify(cond_ty, BOOL, expr.cond.span)
        then_ty = self._block(expr.then_blk)
        if expr.else_blk is None:
            # missing else => then-block must be Unit
            self.unify(then_ty, UNIT, expr.span)
            return then_ty
        if isinstance(expr.else_blk, ast.Block):
            else_ty = self._block(expr.else_blk)
        else:
            else_ty = self._expr(expr.else_blk)
        self.unify(then_ty, else_ty, expr.span)
        return then_ty

    # -------------------------------------------------------------- structs

    def _struct_lit(self, expr: ast.StructLit) -> Type:
        walked = [
            (fname, self._expr(fexpr), fexpr.span) for fname, fexpr in expr.fields
        ]
        rest_ty = self._expr(expr.rest) if expr.rest is not None else None
        if expr.name not in self.struct_fields:
            self._diag("OX0202", f"unknown struct '{expr.name}'", expr.span)
            if rest_ty is not None and expr.rest is not None:
                self.unify(rest_ty, ERROR_TYPE, expr.rest.span)
            return ERROR_TYPE
        fmap = self.struct_fields[expr.name]
        seen: set[str] = set()
        for fname, fty, fspan in walked:
            if fname not in fmap:
                self._diag(
                    "OX0304",
                    f"struct '{expr.name}' has no field '{fname}'",
                    fspan,
                )
                continue
            if fname in seen:
                self._diag(
                    "OX0304", f"duplicate field '{fname}' in literal", fspan
                )
                continue
            seen.add(fname)
            self.unify(fty, fmap[fname], fspan)
        if rest_ty is not None and expr.rest is not None:
            # Functional update (section 36): the listed fields are a subset
            # (missing fields come from rest), and rest must be the same
            # struct type.
            self.unify(rest_ty, TCon(expr.name), expr.rest.span)
            return TCon(expr.name)
        missing = [f for f in fmap if f not in seen]
        if missing:
            self._diag(
                "OX0304",
                f"missing field(s) in '{expr.name}' literal: "
                + ", ".join(f"'{f}'" for f in missing),
                expr.span,
            )
        return TCon(expr.name)

    def _field_access(self, expr: ast.FieldAccess) -> Type:
        obj_ty = self._expr(expr.obj)
        result = self._field_check(obj_ty, expr.field, expr.span)
        if result is not None:
            return result
        # Object type unknown so far: defer until the global solve fills it.
        tv = self._fresh()
        self._pending_fields.append((obj_ty, expr.field, expr.span, tv))
        return tv

    def _field_check(self, obj_ty: Type, fname: str, span: Span) -> Type | None:
        pruned = self._prune(obj_ty)
        if isinstance(pruned, TVar):
            return None
        if isinstance(pruned, TCon):
            if pruned.name == _ERROR_NAME:
                return ERROR_TYPE
            fmap = self.struct_fields.get(pruned.name)
            if fmap is not None:
                if fname not in fmap:
                    self._diag(
                        "OX0304",
                        f"struct '{pruned.name}' has no field '{fname}'",
                        span,
                    )
                    return ERROR_TYPE
                return fmap[fname]
        # OX0306, not OX0304: there is no struct here, so struct-shape
        # guidance ("check field names, duplicates, destructuring") sends
        # the reader hunting for a field that cannot exist. Measured at 10
        # of 29 OX0304 emissions before the split.
        self._diag(
            "OX0306",
            f"field access on non-struct type "
            f"{type_str(self._resolve_full(pruned))}",
            span,
        )
        return ERROR_TYPE

    def _flush_deferred(self) -> None:
        """Alternate the deferred field and ``?`` flushes to a combined
        fixpoint: each may solve metavariables the other is waiting on
        (``p?.f`` defers the field on the try's result and vice versa)."""
        while True:
            before = len(self._pending_fields) + len(self._pending_tries)
            self._flush_pending_fields()
            self._flush_pending_tries()
            if len(self._pending_fields) + len(self._pending_tries) == before:
                return

    def _flush_pending_fields(self) -> None:
        progress = True
        while progress and self._pending_fields:
            progress = False
            remaining: list[tuple[Type, str, Span, Type]] = []
            for obj_ty, fname, span, result_tv in self._pending_fields:
                result = self._field_check(obj_ty, fname, span)
                if result is None:
                    remaining.append((obj_ty, fname, span, result_tv))
                else:
                    self.unify(result_tv, result, span)
                    progress = True
            self._pending_fields = remaining

    # ------------------------------------------------------ post-solve checks

    def _check_main(self) -> None:
        """``fn main`` becomes the Rust entry point (section 21), whose
        type is pinned: no parameters, Unit return."""
        decl = self.resolved.fns.get("main")
        if not isinstance(decl, ast.FnDecl):
            return
        if decl.params:
            self._diag(
                "OX0300",
                "'main' must take no parameters",
                decl.params[0].span,
            )
        _params, ret = self.fn_sigs["main"]
        if self._unsolved_roots(ret):
            return
        final = self._resolve_full(ret)
        if _contains_error(final) or final == UNIT:
            return
        span = decl.ret_ty.span if decl.ret_ty is not None else decl.span
        self._diag(
            "OX0300",
            f"'main' must return Unit, not {type_str(final)}",
            span,
        )

    def _run_op_checks(self) -> None:
        for kind, ty, span, op in self._op_checks:
            full = self._resolve_full(ty)
            roots = self._unsolved_roots(ty)
            if roots:
                continue  # ambiguous: OX0302 is reported elsewhere
            if _contains_error(full):
                continue  # already-poisoned: suppressed
            name = full.name if isinstance(full, TCon) else None
            if kind == "num" and name in _NUMERIC_NAMES:
                continue
            if kind == "int" and name == "Int":
                continue
            self._diag(
                "OX0305",
                f"invalid operand type {type_str(full)} for operator '{op}'",
                span,
            )

    def _finalize(self) -> InferResult:
        reported: set[int] = set()
        # Bindings first (var_id order == source order), then exprs in
        # traversal order; each unsolved metavariable is reported once.
        for var_id in sorted(self.var_tv):
            roots = self._unsolved_roots(self.var_tv[var_id])
            if roots and not roots <= reported:
                info = self.resolved.var_info[var_id]
                self._diag(
                    "OX0302",
                    f"ambiguous type for '{info.name}'",
                    info.def_span,
                )
                reported |= roots
        for node_id, ty in self.node_types.items():
            roots = self._unsolved_roots(ty)
            if roots and not roots <= reported:
                self._diag(
                    "OX0302", "ambiguous type", self.node_spans[node_id]
                )
                reported |= roots
        types = {
            node_id: self._final_type(ty) for node_id, ty in self.node_types.items()
        }
        var_types = {
            var_id: self._final_type(tv) for var_id, tv in self.var_tv.items()
        }
        return InferResult(types=types, var_types=var_types, diagnostics=self.diagnostics)

    def _final_type(self, ty: Type) -> Type:
        if self._unsolved_roots(ty):
            return ERROR_TYPE
        return self._resolve_full(ty)


def _contains_error(ty: Type) -> bool:
    match ty:
        case TCon(name=name, args=args):
            if name == _ERROR_NAME:
                return True
            return any(_contains_error(a) for a in args)
        case TFn(params=params, ret=ret):
            return _contains_error(ret) or any(_contains_error(p) for p in params)
    return False
