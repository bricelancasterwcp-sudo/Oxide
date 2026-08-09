"""The §56 field-assignment deformation signature, with a pinned definition.

A grammar-constrained decoder never rejects a token -- it steers to the
nearest valid string (SPEC §54). Before §56, `=` was inadmissible after a
field path, so `s.f = e` became `s.f == e`: a comparison whose value is
discarded.

PINNED DEFINITION. Parse the submission with the DIALECT-APPROPRIATE
parser; count `ExprStmt` nodes whose expression is a `BinOp` with
``op == "=="`` and a `FieldAccess` left-hand side. Tail-position
occurrences are returned separately and MUST NOT be pooled into the
signature count: a tail `f.x == e` can be a legitimate Bool return, and
pooling them counts the model's own intent as artifact.

DIALECT IS NOT OPTIONAL. The two arms are different languages: `&P` in a
parameter is ordinary explicit-dialect source and a syntax error in core.
The core parser's error recovery swallows the whole function rather than
raising, so scanning explicit source with the core parser returns a WRONG
NUMBER instead of failing -- the exact hazard this file exists to
prevent. Pass ``dialect="explicit"`` for explicit-arm submissions.

Measured over the committed G0 first attempts (oxide arm, `dialect="oxide"`):
18 statement occurrences in 9 of 600 constrained programs, and exactly 0 of
600 unconstrained.

LIMITATION: there are two value-producing positions that fall in the tail
column rather than the signature, and both are counted the same way for the
same reason. First, tail conversion is syntactic and unconditional (it
applies to any block, regardless of the enclosing function's return type),
so a deformed field assignment that happens to be the LAST statement of a
function's body lands in `Block.tail`, not in an `ExprStmt`. Second, an
un-braced match-arm body (`pat => expr,`, with no `{ }`) is parsed as a bare
expression rather than a `Block`, so a deformed assignment sitting there is
likewise never an `ExprStmt` -- a braced arm's `{ ... expr }` is still
covered by the `Block.tail` case above, and this is the un-braced
counterpart. Both positions make the tail column ambiguous in both
directions: a tail-position `f.x == e` may be a legitimate `Bool` return
(`c.r == c.g && c.g == c.b` is correct code), or it may be a deformed
assignment that happened to fall last. Consequently the statement-position
count is a LOWER BOUND on deformation, not an exact count: pooling the two
columns would overcount (folding in legitimate Bool returns), while
treating the signature alone as complete would undercount (missing
deformations that landed in either tail position). The two columns are kept
separate for exactly this reason, and the pre-registered `18 -> 0` endpoint
should be read as a lower-bound claim.

This lives in the repo rather than a scratch script because the 6a pilot's
demand table became irreproducible when its filter did not.
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.explicit.parser import parse_explicit
from src.parser import ast
from src.parser.parser import parse_source

# Dialect -> (source) -> Module. Keys are the eval arm names, so a caller
# can pass a run's `arm` straight through. The `rust` arm is not Oxide and
# has no signature to count.
DIALECTS = ("oxide", "explicit")


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
            return [scrutinee, *arms]
        case ast.MatchArm(body=body):
            return [body]
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


def field_assign_deformations(
    source: str, dialect: str = "oxide"
) -> tuple[int, int]:
    """``(statement_position, tail_position)`` counts for *source*.

    The FIRST element is the signature. The second is reported for honesty
    and must never be pooled into it. Never raises: a submission that does
    not parse contributes ``(0, 0)``.

    *dialect* selects the parser and MUST match the arm the submission came
    from: ``"oxide"`` (the default, the core language) or ``"explicit"``.
    Scanning explicit-arm source with the core parser silently UNDERCOUNTS
    -- `&P` in a parameter is a core syntax error, and the core parser's
    error recovery discards the enclosing function rather than raising, so
    a real signature inside it is never seen. Anything else raises
    ``ValueError``: a typo'd dialect must not degrade into a wrong number.
    """
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; expected one of {DIALECTS}")
    try:
        if dialect == "explicit":
            module, _diags, _parser = parse_explicit(source)
        else:
            module, _diags = parse_source(source)
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
            case ast.MatchArm(body=body) if not isinstance(
                body, ast.Block
            ) and _is_signature(body):
                tail_hits += 1
        stack.extend(_children(node))
    return (stmt_hits, tail_hits)


USAGE = """\
usage: python -m eval.deformation <results-dir>

Scans FIRST attempts of the OXIDE ARM ONLY (`*/raw/*.oxide.1.txt`) with
the core parser -- that is the scope of the pre-registered 18 -> 0
endpoint, and this is the invocation that reproduces it.

Explicit-arm submissions are a different language and are NOT scanned
here: call field_assign_deformations(src, dialect="explicit") directly.
Passing explicit source to the core parser undercounts silently.\
"""


def scan_oxide_arm(root: Path) -> dict[str, tuple[int, int, int, int]]:
    """Per-family ``(progs, stmt, tail, stmt_progs)`` under a run root.

    OXIDE ARM ONLY: the glob is `*/raw/*.oxide.1.txt` and the parser is the
    core one. That is the scope of the pre-registered endpoint. See
    ``USAGE`` for why the explicit arm is not folded in here.
    """
    totals: dict[str, list[int]] = {}
    for raw in sorted(root.glob("*/raw/*.oxide.1.txt")):
        family = raw.parent.parent.name.split("-")[1]
        stmt, tail = field_assign_deformations(
            raw.read_text(encoding="utf-8", errors="replace"),
            dialect="oxide",
        )
        row = totals.setdefault(family, [0, 0, 0, 0])
        row[0] += 1
        row[1] += stmt
        row[2] += tail
        row[3] += 1 if stmt else 0
    return {family: tuple(row) for family, row in totals.items()}


def main() -> None:
    """Scan oxide-arm first attempts under a run root. See ``USAGE``."""
    if len(sys.argv) != 2:
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    totals = scan_oxide_arm(Path(sys.argv[1]))
    print(f"{'family':<14}{'progs':>7}{'stmt':>7}{'tail':>7}{'stmt progs':>12}")
    for family, (progs, stmt, tail, hit) in sorted(totals.items()):
        print(f"{family:<14}{progs:>7}{stmt:>7}{tail:>7}{hit:>12}")


if __name__ == "__main__":
    main()
