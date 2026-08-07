"""AST node definitions and the canonical ``dump()`` printer (SPEC.md §§7-8,
27, 35).

Every node is a frozen, slotted dataclass whose first two fields are
``node_id`` (assigned by the Parser from a per-instance counter) and
``span`` (the node's full source extent in byte offsets). Sequences are
tuples; optional fields are ``None`` when absent.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

from src.diagnostics import Span


@dataclass(frozen=True, slots=True)
class Module:
    node_id: int
    span: Span
    items: tuple[Item, ...]


@dataclass(frozen=True, slots=True)
class FnDecl:
    node_id: int
    span: Span
    name: str
    params: tuple[Param, ...]
    ret_ty: TypeExpr | None
    body: Block


@dataclass(frozen=True, slots=True)
class Param:
    node_id: int
    span: Span
    name: str
    ty: TypeExpr | None


@dataclass(frozen=True, slots=True)
class StructDecl:
    node_id: int
    span: Span
    name: str
    fields: tuple[FieldDef, ...]


@dataclass(frozen=True, slots=True)
class FieldDef:
    node_id: int
    span: Span
    name: str
    ty: TypeExpr


@dataclass(frozen=True, slots=True)
class EnumDecl:
    node_id: int
    span: Span
    name: str
    variants: tuple[tuple[str, tuple[TypeExpr, ...]], ...]


@dataclass(frozen=True, slots=True)
class TypeExpr:
    node_id: int
    span: Span
    name: str
    args: tuple[TypeExpr, ...]


@dataclass(frozen=True, slots=True)
class Block:
    node_id: int
    span: Span
    stmts: tuple[Stmt, ...]
    tail: Expr | None


@dataclass(frozen=True, slots=True)
class Let:
    node_id: int
    span: Span
    pattern: Pattern
    ty: TypeExpr | None
    init: Expr


@dataclass(frozen=True, slots=True)
class BindPat:
    node_id: int
    span: Span
    name: str


@dataclass(frozen=True, slots=True)
class DestructPat:
    node_id: int
    span: Span
    struct_name: str
    field_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Assign:
    node_id: int
    span: Span
    name: str
    value: Expr


@dataclass(frozen=True, slots=True)
class Return:
    node_id: int
    span: Span
    value: Expr | None


@dataclass(frozen=True, slots=True)
class Break:
    node_id: int
    span: Span


@dataclass(frozen=True, slots=True)
class Continue:
    node_id: int
    span: Span


@dataclass(frozen=True, slots=True)
class While:
    node_id: int
    span: Span
    cond: Expr
    body: Block


@dataclass(frozen=True, slots=True)
class For:
    node_id: int
    span: Span
    var: str
    iterable: Expr
    body: Block


@dataclass(frozen=True, slots=True)
class ExprStmt:
    node_id: int
    span: Span
    expr: Expr


@dataclass(frozen=True, slots=True)
class If:
    node_id: int
    span: Span
    cond: Expr
    then_blk: Block
    else_blk: Block | If | None


@dataclass(frozen=True, slots=True)
class Match:
    node_id: int
    span: Span
    scrutinee: Expr
    arms: tuple[MatchArm, ...]


@dataclass(frozen=True, slots=True)
class MatchArm:
    node_id: int
    span: Span
    pattern: VariantPat
    body: Expr | Block


@dataclass(frozen=True, slots=True)
class VariantPat:
    node_id: int
    span: Span
    name: str | None  # None = the wildcard arm pattern `_`
    binders: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Call:
    node_id: int
    span: Span
    callee: Expr
    args: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class BinOp:
    node_id: int
    span: Span
    op: str
    lhs: Expr
    rhs: Expr


@dataclass(frozen=True, slots=True)
class UnOp:
    node_id: int
    span: Span
    op: str
    operand: Expr


@dataclass(frozen=True, slots=True)
class FieldAccess:
    node_id: int
    span: Span
    obj: Expr
    field: str


@dataclass(frozen=True, slots=True)
class StructLit:
    node_id: int
    span: Span
    name: str
    fields: tuple[tuple[str, Expr], ...]
    # v0.2.1 functional update (SPEC.md §§34-35): `S { f: e1, ..rest }`.
    # None when no `..rest` clause is present (additive default so existing
    # construction sites remain valid).
    rest: Expr | None = None


@dataclass(frozen=True, slots=True)
class Try:
    node_id: int
    span: Span
    operand: Expr


@dataclass(frozen=True, slots=True)
class Var:
    node_id: int
    span: Span
    name: str


@dataclass(frozen=True, slots=True)
class Lit:
    node_id: int
    span: Span
    value: object
    kind: str  # "int" | "float" | "str" | "bool"


@dataclass(frozen=True, slots=True)
class ErrorExpr:
    node_id: int
    span: Span


@dataclass(frozen=True, slots=True)
class ErrorStmt:
    node_id: int
    span: Span


type Expr = (
    If
    | Match
    | While
    | For
    | Call
    | BinOp
    | UnOp
    | FieldAccess
    | StructLit
    | Try
    | Var
    | Lit
    | ErrorExpr
)
type Stmt = Let | Assign | Return | Break | Continue | ExprStmt | ErrorStmt
type Pattern = BindPat | DestructPat
type Item = FnDecl | StructDecl | EnumDecl | ErrorStmt


def _sexp(*parts: str) -> str:
    return "(" + " ".join(parts) + ")"


def _lit_repr(value: object, kind: str) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if kind == "float" or kind == "str":
        return repr(value)
    return str(value)


def _dump_node(node: object) -> Generator[object, str, str]:
    """One node's dump as a generator: yields each child node, receives the
    child's rendered text back, and returns the node's own S-expression.

    Driven by the explicit stack in :func:`dump` so rendering never recurses
    on the Python call stack.
    """
    match node:
        case Module(items=items):
            parts: list[str] = []
            for item in items:
                parts.append((yield item))
            return _sexp("module", *parts)
        case FnDecl(name=name, params=params, ret_ty=ret_ty, body=body):
            param_dumps: list[str] = []
            for param in params:
                param_dumps.append((yield param))
            parts = [_sexp("params", *param_dumps)]
            if ret_ty is not None:
                ret_dump = yield ret_ty
                parts.append(f"(ret {ret_dump})")
            parts.append((yield body))
            return _sexp("fn", name, *parts)
        case Param(name=name, ty=ty):
            if ty is None:
                return _sexp("param", name)
            return _sexp("param", name, (yield ty))
        case StructDecl(name=name, fields=fields):
            parts = []
            for field_def in fields:
                parts.append((yield field_def))
            return _sexp("struct", name, *parts)
        case FieldDef(name=name, ty=ty):
            return _sexp("field", name, (yield ty))
        case EnumDecl(name=name, variants=variants):
            parts = []
            for vname, vtys in variants:
                ty_dumps: list[str] = []
                for vty in vtys:
                    ty_dumps.append((yield vty))
                parts.append(_sexp("variant", vname, *ty_dumps))
            return _sexp("enum", name, *parts)
        case TypeExpr(name=name, args=args):
            parts = []
            for arg in args:
                parts.append((yield arg))
            return _sexp("type", name, *parts)
        case Block(stmts=stmts, tail=tail):
            parts = []
            for stmt in stmts:
                parts.append((yield stmt))
            if tail is not None:
                tail_dump = yield tail
                parts.append(f"(tail {tail_dump})")
            return _sexp("block", *parts)
        case Let(pattern=pattern, ty=ty, init=init):
            parts = [(yield pattern)]
            if ty is not None:
                parts.append((yield ty))
            parts.append((yield init))
            return _sexp("let", *parts)
        case BindPat(name=name):
            return _sexp("bind", name)
        case DestructPat(struct_name=struct_name, field_names=field_names):
            return _sexp("destruct", struct_name, *field_names)
        case Assign(name=name, value=value):
            return _sexp("assign", name, (yield value))
        case Return(value=value):
            if value is None:
                return _sexp("return")
            return _sexp("return", (yield value))
        case Break():
            return "(break)"
        case Continue():
            return "(continue)"
        case Try(operand=operand):
            return _sexp("try", (yield operand))
        case While(cond=cond, body=body):
            return _sexp("while", (yield cond), (yield body))
        case For(var=var, iterable=iterable, body=body):
            return _sexp("for", var, (yield iterable), (yield body))
        case ExprStmt(expr=expr):
            return _sexp("exprstmt", (yield expr))
        case If(cond=cond, then_blk=then_blk, else_blk=else_blk):
            cond_dump = yield cond
            then_dump = yield then_blk
            if else_blk is None:
                return _sexp("if", cond_dump, then_dump)
            return _sexp("if", cond_dump, then_dump, (yield else_blk))
        case Match(scrutinee=scrutinee, arms=arms):
            scrut_dump = yield scrutinee
            parts = []
            for arm in arms:
                parts.append((yield arm))
            return _sexp("match", scrut_dump, *parts)
        case MatchArm(pattern=pattern, body=body):
            return _sexp("arm", (yield pattern), (yield body))
        case VariantPat(name=name, binders=binders):
            if name is None:
                return _sexp("vpat", "_")
            return _sexp("vpat", name, *binders)
        case Call(callee=callee, args=args):
            callee_dump = yield callee
            parts = []
            for arg in args:
                parts.append((yield arg))
            return _sexp("call", callee_dump, *parts)
        case BinOp(op=op, lhs=lhs, rhs=rhs):
            return _sexp("bin", op, (yield lhs), (yield rhs))
        case UnOp(op=op, operand=operand):
            return _sexp("un", op, (yield operand))
        case FieldAccess(obj=obj, field=field):
            return _sexp("field", (yield obj), field)
        case StructLit(name=name, fields=fields, rest=rest):
            parts = []
            for fname, fexpr in fields:
                fexpr_dump = yield fexpr
                parts.append(f"({fname} {fexpr_dump})")
            if rest is not None:
                rest_dump = yield rest
                parts.append(f"(rest {rest_dump})")
            return _sexp("structlit", name, *parts)
        case Var(name=name):
            return _sexp("var", name)
        case Lit(value=value, kind=kind):
            return _sexp("lit", kind, _lit_repr(value, kind))
        case ErrorExpr() | ErrorStmt():
            return "(error)"
        case _:
            raise TypeError(f"dump: not an AST node: {node!r}")


def dump(node: object) -> str:
    """Render a node as its canonical S-expression (SPEC.md §8).

    ``node_id`` and ``span`` are excluded; children are space-separated.
    The traversal uses an explicit generator stack instead of Python
    recursion, so arbitrarily deep ASTs (e.g. long left-associative
    operator chains) render without overflowing the call stack.
    """
    stack: list[Generator[object, str, str]] = [_dump_node(node)]
    child_dump: str | None = None
    while True:
        try:
            child = stack[-1].send(child_dump)  # type: ignore[arg-type]
        except StopIteration as done:
            stack.pop()
            if not stack:
                return done.value
            child_dump = done.value
        else:
            stack.append(_dump_node(child))
            child_dump = None
