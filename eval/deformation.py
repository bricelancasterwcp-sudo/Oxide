"""The §56 field-assignment deformation signature, with a pinned definition.

A grammar-constrained decoder never rejects a token -- it steers to the
nearest valid string (SPEC §54). Before §56, `=` was inadmissible after a
field path, so `s.f = e` became `s.f == e`: a comparison whose value is
discarded.

PINNED DEFINITION. Parse the submission; count `ExprStmt` nodes whose
expression is a `BinOp` with ``op == "=="`` and a `FieldAccess` left-hand
side. Tail-position occurrences are returned separately and MUST NOT be
pooled into the signature count: a tail `f.x == e` can be a legitimate Bool
return, and pooling them counts the model's own intent as artifact.

Measured over the committed G0 first attempts (oxide arm): 18 statement
occurrences in 9 of 600 constrained programs, and exactly 0 of 600
unconstrained.

LIMITATION: tail conversion is syntactic and unconditional (it applies to
any block, regardless of the enclosing function's return type), so a
deformed field assignment that happens to be the LAST statement of a
function's body lands in `Block.tail`, not in an `ExprStmt` -- it is
counted in the tail column, never in the signature. This makes the tail
column ambiguous in both directions: a tail-position `f.x == e` may be a
legitimate `Bool` return (`c.r == c.g && c.g == c.b` is correct code), or it
may be a deformed assignment that happened to fall last. Consequently the
statement-position count is a LOWER BOUND on deformation, not an exact
count: pooling the two columns would overcount (folding in legitimate Bool
returns), while treating the signature alone as complete would undercount
(missing deformations that landed in tail position). The two columns are
kept separate for exactly this reason, and the pre-registered `18 -> 0`
endpoint should be read as a lower-bound claim.

This lives in the repo rather than a scratch script because the 6a pilot's
demand table became irreproducible when its filter did not.
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.parser import ast
from src.parser.parser import parse_source


def _children(node: object) -> list[object]:
    """Every child of *node* that can contain a statement or expression."""
    match node:
        case ast.Module(items=items):
            return list(items)
        case ast.FnDecl(body=body):
            return [body]
        case ast.Block(stmts=stmts, tail=tail):
            return [*stmts, *([tail] if tail is not None else [])]
        case ast.ExprStmt(expr=expr):
            return [expr]
        case ast.Let(init=init):
            return [init]
        case ast.Assign(value=value):
            return [value]
        case ast.FieldAssign(value=value):
            return [value]
        case ast.Return(value=value):
            return [value] if value is not None else []
        case ast.If(cond=cond, then_blk=then_blk, else_blk=else_blk):
            return [cond, then_blk, *([else_blk] if else_blk is not None else [])]
        case ast.While(cond=cond, body=body):
            return [cond, body]
        case ast.For(iterable=iterable, body=body):
            return [iterable, body]
        case ast.Match(scrutinee=scrutinee, arms=arms):
            return [scrutinee, *(arm.body for arm in arms)]
        case ast.Call(callee=callee, args=args):
            return [callee, *args]
        case ast.BinOp(lhs=lhs, rhs=rhs):
            return [lhs, rhs]
        case ast.UnOp(operand=operand):
            return [operand]
        case ast.FieldAccess(obj=obj):
            return [obj]
        case ast.Try(operand=operand):
            return [operand]
        case ast.StructLit(fields=fields, rest=rest):
            return [e for _n, e in fields] + ([rest] if rest is not None else [])
    return []


def _is_signature(expr: object) -> bool:
    return (
        isinstance(expr, ast.BinOp)
        and expr.op == "=="
        and isinstance(expr.lhs, ast.FieldAccess)
    )


def field_assign_deformations(source: str) -> tuple[int, int]:
    """``(statement_position, tail_position)`` counts for *source*.

    The FIRST element is the signature. The second is reported for honesty
    and must never be pooled into it. Never raises: a submission that does
    not parse contributes ``(0, 0)``.
    """
    try:
        module, _ = parse_source(source)
    except Exception:  # a malformed submission is not a signature
        return (0, 0)
    stmt_hits = tail_hits = 0
    stack: list[object] = [module]
    while stack:
        node = stack.pop()
        match node:
            case ast.ExprStmt(expr=expr) if _is_signature(expr):
                stmt_hits += 1
            case ast.Block(tail=tail) if tail is not None and _is_signature(tail):
                tail_hits += 1
        stack.extend(_children(node))
    return (stmt_hits, tail_hits)


def main() -> None:
    """Scan first attempts under a run root: `python -m eval.deformation DIR`."""
    if len(sys.argv) != 2:
        print("usage: python -m eval.deformation <results-dir>", file=sys.stderr)
        raise SystemExit(2)
    root = Path(sys.argv[1])
    totals: dict[str, list[int]] = {}
    for raw in sorted(root.glob("*/raw/*.oxide.1.txt")):
        family = raw.parent.parent.name.split("-")[1]
        stmt, tail = field_assign_deformations(
            raw.read_text(encoding="utf-8", errors="replace")
        )
        row = totals.setdefault(family, [0, 0, 0, 0])
        row[0] += 1
        row[1] += stmt
        row[2] += tail
        row[3] += 1 if stmt else 0
    print(f"{'family':<14}{'progs':>7}{'stmt':>7}{'tail':>7}{'stmt progs':>12}")
    for family, (progs, stmt, tail, hit) in sorted(totals.items()):
        print(f"{family:<14}{progs:>7}{stmt:>7}{tail:>7}{hit:>12}")


if __name__ == "__main__":
    main()
