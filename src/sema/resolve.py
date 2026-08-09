"""Name resolution (SPEC.md sections 15, 28, 36).

Maps every local variable use to its binding, every call to its global
callee (user fn or builtin), and collects top-level declarations.

var_id numbering: one per-module counter starting at 0, assigned in
source order of binding sites — for each function its params left to
right, then body binders in pre-order. Shadowing introduces a fresh
var_id. A destructure pattern binds fields in the order they appear in
the pattern's own field list; match-arm binders and for-loop variables
are ordinary body binders scoped to their arm/body.

Variant names live in the single top-level namespace and must be
globally unique (section 28); the builtin ``Option``/``Result`` variant
names are reserved. Variant references (bare nullary values and
constructor calls) are recorded in ``variant_refs`` for infer, which
owns the usage/arity checks.

Diagnostics: OX0200 unknown identifier (including unknown assignment
targets), OX0201 function/builtin used as a value, OX0203 duplicate
top-level name (including builtin and reserved-variant clashes), OX0204
duplicate binder within one fn's params or one pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.diagnostics import Diagnostic, Span
from src.parser import ast
from src.sema.types import BUILTIN_ENUMS, BUILTIN_VARIANTS, BUILTINS

# Section 14 fixes these as the built-in types (extended by section 28's
# builtin enums); a user struct/enum may not shadow them.
_RESERVED_TYPE_NAMES = frozenset(
    {"Int", "Float", "Bool", "Str", "Unit", "Vec", *BUILTIN_ENUMS}
)


@dataclass(frozen=True, slots=True)
class VarInfo:
    """Metadata for one local binding."""

    var_id: int
    name: str
    fn: str
    def_span: Span


@dataclass
class ResolveResult:
    """Output of :func:`resolve` (SPEC.md section 15)."""

    use_of: dict[int, int] = field(default_factory=dict)
    binds_of: dict[int, tuple[int, ...]] = field(default_factory=dict)
    var_info: dict[int, VarInfo] = field(default_factory=dict)
    callee_of: dict[int, str] = field(default_factory=dict)
    fns: dict[str, object] = field(default_factory=dict)
    structs: dict[str, object] = field(default_factory=dict)
    enums: dict[str, object] = field(default_factory=dict)
    # user variant name -> owning enum name (builtins live in types.py)
    variants: dict[str, str] = field(default_factory=dict)
    # Var node_id (bare variant value) or Call node_id (constructor call)
    # -> variant name; infer owns the usage/arity checks (OX0303/OX0307)
    variant_refs: dict[int, str] = field(default_factory=dict)
    # Assign/FieldAssign node_id -> the assigned variable's var_id
    # (the BASE, for a field write)
    assign_of: dict[int, int] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)


def resolve(module: ast.Module) -> ResolveResult:
    """Resolve a parsed module. Never raises."""
    return _Resolver().run(module)


class _Resolver:
    def __init__(self) -> None:
        self.result = ResolveResult()
        self._next_var_id = 0
        self._scopes: list[dict[str, int]] = []
        self._fn_name = ""
        # variant name -> span of the declaring enum (for OX0203 notes)
        self._variant_spans: dict[str, Span] = {}

    # ------------------------------------------------------------- helpers

    def _diag(
        self,
        code: str,
        message: str,
        span: Span,
        notes: tuple[tuple[str, Span], ...] = (),
    ) -> None:
        self.result.diagnostics.append(Diagnostic(code, message, span, notes))

    def _new_var(self, name: str, span: Span) -> int:
        var_id = self._next_var_id
        self._next_var_id += 1
        self.result.var_info[var_id] = VarInfo(var_id, name, self._fn_name, span)
        return var_id

    def _lookup(self, name: str) -> int | None:
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None

    # ---------------------------------------------------------- module pass

    def run(self, module: ast.Module) -> ResolveResult:
        for item in module.items:
            if isinstance(item, (ast.FnDecl, ast.StructDecl, ast.EnumDecl)):
                self._declare_item(item)
        for item in module.items:
            if isinstance(item, ast.FnDecl):
                self._resolve_fn(item)
        return self.result

    def _prior_span(self, name: str) -> Span | None:
        """Span of an existing top-level definition of ``name``, if any."""
        prior = (
            self.result.fns.get(name)
            or self.result.structs.get(name)
            or self.result.enums.get(name)
        )
        if prior is not None:
            return prior.span  # type: ignore[union-attr]
        return self._variant_spans.get(name)

    def _declare_item(
        self, item: ast.FnDecl | ast.StructDecl | ast.EnumDecl
    ) -> None:
        name = item.name
        if (
            isinstance(item, (ast.StructDecl, ast.EnumDecl))
            and name in _RESERVED_TYPE_NAMES
        ):
            kind = "struct" if isinstance(item, ast.StructDecl) else "enum"
            self._diag(
                "OX0203",
                f"{kind} '{name}' clashes with a built-in type name",
                item.span,
            )
            return
        if not self._declare_name(name, item.span):
            return
        if isinstance(item, ast.FnDecl):
            self.result.fns[name] = item
        elif isinstance(item, ast.StructDecl):
            self.result.structs[name] = item
        else:
            self.result.enums[name] = item
            for vname, _payload_tys in item.variants:
                if self._declare_name(vname, item.span):
                    self.result.variants[vname] = name
                    self._variant_spans[vname] = item.span

    def _declare_name(self, name: str, span: Span) -> bool:
        """Enforce the single top-level namespace (sections 15, 28).

        Returns True when ``name`` is free; otherwise reports OX0203
        (builtin fn, reserved builtin variant, or an earlier definition).
        """
        prior_span = self._prior_span(name)
        if name in BUILTINS or name in BUILTIN_VARIANTS or prior_span is not None:
            notes: tuple[tuple[str, Span], ...] = ()
            if prior_span is not None:
                notes = (("previous definition here", prior_span),)
            self._diag("OX0203", f"duplicate top-level name '{name}'", span, notes)
            return False
        return True

    # ------------------------------------------------------------ functions

    def _resolve_fn(self, fn: ast.FnDecl) -> None:
        self._fn_name = fn.name
        param_scope: dict[str, int] = {}
        seen: set[str] = set()
        for param in fn.params:
            if param.name in seen:
                self._diag(
                    "OX0204",
                    f"duplicate parameter '{param.name}'",
                    param.span,
                )
            seen.add(param.name)
            var_id = self._new_var(param.name, param.span)
            self.result.binds_of[param.node_id] = (var_id,)
            param_scope[param.name] = var_id
        self._scopes.append(param_scope)
        self._block(fn.body)
        self._scopes.pop()

    # --------------------------------------------------------------- blocks

    def _block(self, block: ast.Block) -> None:
        self._scopes.append({})
        for stmt in block.stmts:
            self._stmt(stmt)
        if block.tail is not None:
            self._expr(block.tail)
        self._scopes.pop()

    def _stmt(self, stmt: ast.Stmt) -> None:
        match stmt:
            case ast.Let(pattern=pattern, init=init):
                bindings = self._pattern_binders(pattern)
                self._expr(init)
                scope = self._scopes[-1]
                for name, var_id in bindings:
                    scope[name] = var_id
            case ast.Assign(name=name, value=value):
                var_id = self._lookup(name)
                if var_id is not None:
                    self.result.assign_of[stmt.node_id] = var_id
                else:
                    # Section 28: the target must be an existing local/param.
                    self._diag(
                        "OX0200",
                        f"unknown identifier '{name}' in assignment",
                        stmt.span,
                    )
                self._expr(value)
            case ast.FieldAssign(base=base, value=value):
                # Section 56: the base must be an existing local/param, and
                # goes in the SAME map as whole-variable assignment so §28's
                # "an assigned param gets mode own" applies unchanged -- a
                # soundness requirement, since a field write through &T is
                # rustc E0594.
                var_id = self._lookup(base)
                if var_id is not None:
                    self.result.assign_of[stmt.node_id] = var_id
                else:
                    self._diag(
                        "OX0200",
                        f"unknown identifier '{base}' in assignment",
                        stmt.span,
                    )
                self._expr(value)
            case ast.Return(value=value):
                if value is not None:
                    self._expr(value)
            case ast.Break() | ast.Continue():
                # Loop scoping is the parser's job (OX0105); nothing binds.
                pass
            case ast.ExprStmt(expr=expr):
                self._expr(expr)
            case ast.ErrorStmt():
                pass

    def _pattern_binders(self, pattern: ast.Pattern) -> list[tuple[str, int]]:
        """Assign var_ids for a let pattern (source order), returning the
        (name, var_id) pairs to insert into scope AFTER the initializer."""
        match pattern:
            case ast.BindPat(name=name):
                var_id = self._new_var(name, pattern.span)
                self.result.binds_of[pattern.node_id] = (var_id,)
                return [(name, var_id)]
            case ast.DestructPat(field_names=field_names):
                seen: set[str] = set()
                pairs: list[tuple[str, int]] = []
                ids: list[int] = []
                for fname in field_names:
                    if fname in seen:
                        self._diag(
                            "OX0204",
                            f"duplicate binder '{fname}' in pattern",
                            pattern.span,
                        )
                    seen.add(fname)
                    var_id = self._new_var(fname, pattern.span)
                    ids.append(var_id)
                    pairs.append((fname, var_id))
                self.result.binds_of[pattern.node_id] = tuple(ids)
                return pairs
        return []

    # ---------------------------------------------------------- expressions

    def _expr(self, expr: ast.Expr) -> None:
        match expr:
            case ast.Var():
                self._var_use(expr)
            case ast.Lit() | ast.ErrorExpr():
                pass
            case ast.Call(callee=callee, args=args):
                self._callee(expr, callee)
                for arg in args:
                    self._expr(arg)
            case ast.BinOp(lhs=lhs, rhs=rhs):
                self._expr(lhs)
                self._expr(rhs)
            case ast.UnOp(operand=operand):
                self._expr(operand)
            case ast.FieldAccess(obj=obj):
                self._expr(obj)
            case ast.Try(operand=operand):
                self._expr(operand)
            case ast.StructLit(fields=fields, rest=rest):
                for _name, value in fields:
                    self._expr(value)
                if rest is not None:
                    self._expr(rest)
            case ast.If(cond=cond, then_blk=then_blk, else_blk=else_blk):
                self._expr(cond)
                self._block(then_blk)
                if isinstance(else_blk, ast.Block):
                    self._block(else_blk)
                elif isinstance(else_blk, ast.If):
                    self._expr(else_blk)
            case ast.While(cond=cond, body=body):
                self._expr(cond)
                self._block(body)
            case ast.For(var=var_name, iterable=iterable, body=body):
                # The loop variable's binding site precedes the iterable in
                # source order, but is NOT in scope for the iterable.
                var_id = self._new_var(var_name, expr.span)
                self.result.binds_of[expr.node_id] = (var_id,)
                self._expr(iterable)
                self._scopes.append({var_name: var_id})
                self._block(body)
                self._scopes.pop()
            case ast.Match(scrutinee=scrutinee, arms=arms):
                self._expr(scrutinee)
                for arm in arms:
                    self._match_arm(arm)

    def _match_arm(self, arm: ast.MatchArm) -> None:
        """Bind an arm's pattern binders (fresh, scoped to the arm body).

        Arm variant names are validated by infer (OX0307); resolve only
        assigns var_ids and enforces OX0204 within the one pattern.
        """
        pat = arm.pattern
        seen: set[str] = set()
        ids: list[int] = []
        scope: dict[str, int] = {}
        for bname in pat.binders:
            if bname in seen:
                self._diag(
                    "OX0204", f"duplicate binder '{bname}' in pattern", pat.span
                )
            seen.add(bname)
            var_id = self._new_var(bname, pat.span)
            ids.append(var_id)
            scope[bname] = var_id
        self.result.binds_of[pat.node_id] = tuple(ids)
        self._scopes.append(scope)
        if isinstance(arm.body, ast.Block):
            self._block(arm.body)
        else:
            self._expr(arm.body)
        self._scopes.pop()

    def _callee(self, call: ast.Call, callee: ast.Expr) -> None:
        if not isinstance(callee, ast.Var):
            self._expr(callee)
            return
        var_id = self._lookup(callee.name)
        if var_id is not None:
            # Local bindings shadow global fn names.
            self.result.use_of[callee.node_id] = var_id
        elif callee.name in self.result.fns or callee.name in BUILTINS:
            self.result.callee_of[call.node_id] = callee.name
        elif callee.name in self.result.variants or callee.name in BUILTIN_VARIANTS:
            # Variant constructor call; infer checks payload arity (OX0303).
            self.result.variant_refs[call.node_id] = callee.name
        else:
            self._diag(
                "OX0200", f"unknown identifier '{callee.name}'", callee.span
            )

    def _var_use(self, var: ast.Var) -> None:
        var_id = self._lookup(var.name)
        if var_id is not None:
            self.result.use_of[var.node_id] = var_id
            return
        if var.name in self.result.variants or var.name in BUILTIN_VARIANTS:
            # Bare variant value; infer rejects payload variants (OX0303).
            self.result.variant_refs[var.node_id] = var.name
            return
        if var.name in self.result.fns or var.name in BUILTINS:
            self._diag(
                "OX0201",
                f"function '{var.name}' may only be used as a call target",
                var.span,
            )
            return
        self._diag("OX0200", f"unknown identifier '{var.name}'", var.span)
