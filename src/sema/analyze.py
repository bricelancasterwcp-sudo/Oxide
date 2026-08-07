"""Full semantic-analysis pipeline and blind-test surface (SPEC.md section 15).

Pipeline: lex+parse -> resolve -> infer -> modes -> linear, with the
section-15 gates: parse (or lex) errors skip sema entirely; resolve
errors skip infer/modes/linear; infer errors skip modes/linear. A
skipped phase contributes an empty result. Drop suppression for
functions with linear diagnostics is applied inside ``check_linear``.

``analyze`` never raises on any input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.diagnostics import Diagnostic
from src.parser import ast
from src.parser.parser import parse_source
from src.sema.infer import InferResult, infer
from src.sema.linear import LinearResult, check_linear
from src.sema.modes import ModeResult, infer_modes
from src.sema.resolve import ResolveResult, resolve
from src.sema.types import ERROR_TYPE, type_str


@dataclass
class SemaResult:
    """Everything the pipeline produced, in phase order."""

    module: ast.Module
    resolve: ResolveResult
    infer: InferResult
    modes: ModeResult
    linear: LinearResult
    diagnostics: list[Diagnostic] = field(default_factory=list)


def analyze(source: str) -> SemaResult:
    """Run the whole pipeline over ``source``. NEVER raises."""
    module, front_diags = parse_source(source)
    diagnostics = list(front_diags)
    resolve_res = ResolveResult()
    infer_res = InferResult()
    modes_res = ModeResult()
    linear_res = LinearResult()
    if not diagnostics:
        resolve_res = resolve(module)
        diagnostics.extend(resolve_res.diagnostics)
        if not resolve_res.diagnostics:
            infer_res = infer(module, resolve_res)
            diagnostics.extend(infer_res.diagnostics)
            if not infer_res.diagnostics:
                modes_res = infer_modes(module, resolve_res, infer_res)
                linear_res = check_linear(
                    module, resolve_res, infer_res, modes_res
                )
                diagnostics.extend(linear_res.diagnostics)
    return SemaResult(
        module, resolve_res, infer_res, modes_res, linear_res, diagnostics
    )


def diag_codes(res: SemaResult) -> list[str]:
    """All diagnostic codes in phase order."""
    return [diag.code for diag in res.diagnostics]


def var_types_by_name(res: SemaResult, fn: str, name: str) -> list[str]:
    """Rendered type of each binding of ``name`` in ``fn``, binding order."""
    out: list[str] = []
    for var_id in sorted(res.resolve.var_info):
        info = res.resolve.var_info[var_id]
        if info.fn == fn and info.name == name:
            out.append(type_str(res.infer.var_types.get(var_id, ERROR_TYPE)))
    return out


def use_classes(res: SemaResult, fn: str, name: str) -> list[str]:
    """Use classes ('copy'|'read'|'move') of ``name``'s uses, source order."""
    out: list[str] = []
    for node_id, cls in res.linear.use_class.items():
        var_id = res.resolve.use_of.get(node_id)
        if var_id is None:
            continue
        info = res.resolve.var_info.get(var_id)
        if info is not None and info.fn == fn and info.name == name:
            out.append(cls)
    return out


def param_modes(res: SemaResult, fn: str) -> tuple[str, ...]:
    """Inferred parameter modes for ``fn`` (empty if modes were skipped)."""
    return tuple(res.modes.modes.get(fn, ()))


def drop_list(res: SemaResult) -> list[tuple[str, str, str]]:
    """Sorted (fn, var_name, kind) triples for every DropPoint."""
    return sorted(
        (drop.fn, drop.var_name, drop.kind) for drop in res.linear.drops
    )
