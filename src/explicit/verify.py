"""Verification of written annotations against core analysis truth
(SPEC.md section 41).

Runs over the STRIPPED core AST and a clean core :class:`SemaResult`,
diffing what the dialect program wrote against what the unchanged core
analysis computed:

- ``&`` on a move-class or copy-class use  -> EX0001
- bare read-class use of a non-copy value  -> EX0002
- missing written drop for a core DropPoint -> EX0003
- written drop matching no core DropPoint   -> EX0004
- parameter ``&`` disagreeing with the inferred mode -> EX0005

Exemptions (no written drop expected or allowed): ``<temp>`` drops;
branch-end DropPoints anchored at a loop (codegen delegates those to the
target's conditional scope drop — there is no syntactic place to write
them); and block-end DropPoints anchored at a SYNTHETIC block — the
wrapper ``src.sema.cfg`` fabricates around a chained ``else if``
(``_if``) or an expression-body match arm (``_match``), whose span is
the chained If's / the arm expression's span. No drop statement is
syntactically writable at those anchors either, and core codegen on the
stripped AST synthesizes the drops itself (nested-else rewrite / block-
arm rewrite). Branch-end points anchored at an If ARE required, written
as a drops-only ``else`` block (stripped to the core absent-else form).
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Iterator

from src.diagnostics import Diagnostic, Span
from src.explicit.strip import Annotations, WrittenDrop, _key
from src.parser import ast
from src.sema.analyze import SemaResult
from src.sema.linear import DropPoint
from src.sema.types import ERROR_TYPE, is_copy, type_str

# Pinned dialect suggestion strings (SPEC.md section 41), keyed by code —
# consumed by the CLI's --json rendering exactly like the section-40 table.
EX_SUGGESTIONS: dict[str, str] = {
    "EX0001": "This use consumes the value; remove the &.",
    "EX0002": "This use only reads the value; write &name.",
    "EX0003": "This value's last use is here; add 'drop name' at the required point.",
    "EX0004": (
        "No drop belongs here: the value is not owned/dead at this point. "
        "Remove or move this drop."
    ),
    "EX0005": (
        "Parameter mode is wrong: read-only parameters are declared "
        "name: &Type, consumed parameters name: Type."
    ),
}


def verify_annotations(
    module: ast.Module, ann: Annotations, res: SemaResult
) -> list[Diagnostic]:
    """Diff the written annotations against analysis truth; never raises.

    Precondition: ``res`` is the clean core analysis of ``module``.
    """
    diags: list[Diagnostic] = []
    _check_params(module, ann, res, diags)
    _check_uses(module, ann, res, diags)
    _check_drops(module, ann, res, diags)
    diags.sort(key=lambda d: (d.span.start, d.span.end, d.code))
    return diags


# ---- generic AST walk ------------------------------------------------------


def _walk(node: object) -> Iterator[object]:
    """Every dataclass AST node under ``node`` (Spans excluded)."""
    stack: list[object] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, tuple):
            stack.extend(item)
            continue
        if not is_dataclass(item) or isinstance(item, (Span, type)):
            continue
        yield item
        for f in fields(item):
            value = getattr(item, f.name)
            if isinstance(value, tuple) or is_dataclass(value):
                stack.append(value)


# ---- EX0005: parameter modes -----------------------------------------------


def _check_params(
    module: ast.Module,
    ann: Annotations,
    res: SemaResult,
    diags: list[Diagnostic],
) -> None:
    for item in module.items:
        if not isinstance(item, ast.FnDecl):
            continue
        modes = res.modes.modes.get(item.name, ())
        for index, param in enumerate(item.params):
            bound = res.resolve.binds_of.get(param.node_id, ())
            if not bound:
                continue
            ty = res.infer.var_types.get(bound[0], ERROR_TYPE)
            needs_amp = (
                index < len(modes)
                and modes[index] == "read"
                and not is_copy(ty)
            )
            has_amp = param.node_id in ann.amp_params
            if has_amp == needs_amp:
                continue
            if needs_amp:
                message = (
                    f"parameter '{param.name}' is read-only and must be "
                    f"declared '{param.name}: &{type_str(ty)}'"
                )
                span = param.span
            elif index < len(modes) and modes[index] == "own":
                message = (
                    f"parameter '{param.name}' is consumed and must be "
                    f"declared without '&'"
                )
                span = ann.amp_params[param.node_id]
            else:
                message = (
                    f"parameter '{param.name}' is copy-typed and must be "
                    f"declared without '&'"
                )
                span = ann.amp_params[param.node_id]
            diags.append(Diagnostic("EX0005", message, span))


# ---- EX0001 / EX0002: use-site '&' -----------------------------------------


def _check_uses(
    module: ast.Module,
    ann: Annotations,
    res: SemaResult,
    diags: list[Diagnostic],
) -> None:
    use_of = res.resolve.use_of
    use_class = res.linear.use_class
    for node in _walk(module):
        if not isinstance(node, ast.Var):
            continue
        amp = node.node_id in ann.amp_uses
        var_id = use_of.get(node.node_id)
        if var_id is None:
            # Not a local-variable use (global fn name as callee, bare
            # variant value): '&' never belongs here.
            if amp:
                diags.append(
                    Diagnostic(
                        "EX0001",
                        f"'&' is not allowed on '{node.name}' here; "
                        f"remove the &",
                        node.span,
                    )
                )
            continue
        cls = use_class.get(node.node_id, "copy")
        if cls == "read":
            if not amp:
                diags.append(
                    Diagnostic(
                        "EX0002",
                        f"this use only reads '{node.name}'; "
                        f"write '&{node.name}'",
                        node.span,
                    )
                )
        elif amp:
            if cls == "move":
                message = f"this use consumes '{node.name}'; remove the &"
            else:
                message = (
                    f"'{node.name}' is copy-typed and always written bare; "
                    f"remove the &"
                )
            diags.append(Diagnostic("EX0001", message, node.span))


# ---- EX0003 / EX0004: drop statements --------------------------------------


def _required_drops(
    module: ast.Module, res: SemaResult
) -> list[DropPoint]:
    """Core DropPoints a written ``drop`` must cover: named vars only,
    branch-end only when anchored at an If (loop-anchored branch-end
    drops are codegen-delegated conditional scope drops), and block-end
    only when anchored at a real block. Block-end points anchored at a
    synthetic cfg block — a chained ``else if`` (a merge-hoisted drop on
    the chain edge) or an expression-body match arm (an unconsumed
    binder or merge-hoisted outer var) — have no syntactically writable
    position; codegen synthesizes those drops from the stripped AST."""
    if_spans: set[tuple[int, int]] = set()
    synthetic_spans: set[tuple[int, int]] = set()
    for node in _walk(module):
        if isinstance(node, ast.If):
            if_spans.add(_key(node.span))
            if isinstance(node.else_blk, ast.If):
                synthetic_spans.add(_key(node.else_blk.span))
        elif isinstance(node, ast.MatchArm) and not isinstance(
            node.body, ast.Block
        ):
            synthetic_spans.add(_key(node.body.span))
    return [
        drop
        for drop in res.linear.drops
        if drop.var_id >= 0
        and (drop.kind != "branch-end" or _key(drop.anchor_span) in if_spans)
        and (
            drop.kind != "block-end"
            or _key(drop.anchor_span) not in synthetic_spans
        )
    ]


def _check_drops(
    module: ast.Module,
    ann: Annotations,
    res: SemaResult,
    diags: list[Diagnostic],
) -> None:
    unmatched_written: list[WrittenDrop] = list(ann.drops)
    unmatched_expected: list[DropPoint] = []
    for expected in _required_drops(module, res):
        need = (expected.kind, _key(expected.anchor_span))
        match = next(
            (
                w
                for w in unmatched_written
                if w.fn == expected.fn
                and w.name == expected.var_name
                and need in w.candidates
            ),
            None,
        )
        if match is not None:
            unmatched_written.remove(match)
        else:
            unmatched_expected.append(expected)
    for written in unmatched_written:
        # A same-variable unmet requirement makes this a MISPLACED drop:
        # report the written site once (EX0004) instead of an EX0003/
        # EX0004 pair for what is one mistake.
        paired = next(
            (
                d
                for d in unmatched_expected
                if d.fn == written.fn and d.var_name == written.name
            ),
            None,
        )
        if paired is not None:
            unmatched_expected.remove(paired)
        diags.append(
            Diagnostic(
                "EX0004",
                f"no drop of '{written.name}' belongs at this point",
                written.span,
            )
        )
    for expected in unmatched_expected:
        diags.append(
            Diagnostic(
                "EX0003",
                f"missing 'drop {expected.var_name}' required here "
                f"({expected.kind})",
                expected.anchor_span,
            )
        )
