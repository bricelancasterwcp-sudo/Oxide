"""Explicit-Oxide pipeline entry (SPEC.md section 41).

``run(source)`` mirrors the shape of :func:`src.codegen.rust.transpile`:
``(rust_text, [])`` on success, ``(None, diagnostics)`` otherwise, so
the CLI composes ``--dialect=explicit`` with ``--json``/``--check``
unchanged. Never raises.

Stages: dialect parse -> strip annotations to a core AST (recording
where they were) -> the UNCHANGED core analysis (same phase gates as
``src.sema.analyze.analyze``) -> diff written annotations against
analysis truth (EX0001-EX0005) -> on success, core codegen on the
stripped AST (byte-identical Rust to the core program).
"""

from __future__ import annotations

from src.codegen.rust import emit_rust
from src.diagnostics import Diagnostic
from src.explicit.parser import parse_explicit
from src.explicit.strip import strip_module
from src.explicit.verify import verify_annotations
from src.parser import ast
from src.sema.analyze import SemaResult
from src.sema.infer import InferResult, infer
from src.sema.linear import LinearResult, check_linear
from src.sema.modes import ModeResult, infer_modes
from src.sema.resolve import ResolveResult, resolve


def analyze_module(module: ast.Module) -> SemaResult:
    """The unchanged core semantic pipeline over an already-parsed module,
    with the exact section-15 gates of ``src.sema.analyze.analyze``
    (which this dialect cannot call directly only because it starts from
    source text)."""
    diagnostics: list[Diagnostic] = []
    resolve_res = resolve(module)
    infer_res = InferResult()
    modes_res = ModeResult()
    linear_res = LinearResult()
    diagnostics.extend(resolve_res.diagnostics)
    if not resolve_res.diagnostics:
        infer_res = infer(module, resolve_res)
        diagnostics.extend(infer_res.diagnostics)
        if not infer_res.diagnostics:
            modes_res = infer_modes(module, resolve_res, infer_res)
            linear_res = check_linear(module, resolve_res, infer_res, modes_res)
            diagnostics.extend(linear_res.diagnostics)
    return SemaResult(
        module, resolve_res, infer_res, modes_res, linear_res, diagnostics
    )


def run(source: str) -> tuple[str | None, list[Diagnostic]]:
    """Transpile explicit-Oxide source. Never raises.

    Returns ``(rust_text, [])`` on success; ``(None, diagnostics)`` when
    the dialect front end, the core analysis of the stripped program, or
    the annotation diff (EX codes) reports anything.
    """
    module, front_diags, parser = parse_explicit(source)
    if front_diags:
        return None, front_diags
    stripped, ann = strip_module(module, parser.amp_uses, parser.amp_params)
    res = analyze_module(stripped)
    if res.diagnostics:
        return None, res.diagnostics
    ex_diags = verify_annotations(stripped, ann, res)
    if ex_diags:
        return None, ex_diags
    return emit_rust(res), []


# CLI-facing alias: the dialect's analogue of src.codegen.rust.transpile.
transpile = run
