"""Parameter ownership modes via a call-graph fixpoint (SPEC.md
sections 15, 28).

Every user-function parameter starts optimistically as ``'read'``; a
parameter becomes ``'own'`` iff some path uses it in a MOVE context per
the section-17 table under the current assumptions about every callee's
modes. Flips are monotone (read -> own only), so iterating over the
call graph converges. Copy-typed parameters are always ``'read'``
(copy locals never produce move events) — EXCEPT that a param that is
ever assigned gets mode ``'own'`` unconditionally (section 28 amends
section 15: the callee overwrites the caller's value, so a borrow
cannot do). Pure recursion with no other evidence converges to
``'read'``. Builtin modes are fixed and included in the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.parser import ast
from src.sema import cfg
from src.sema.infer import InferResult
from src.sema.resolve import ResolveResult
from src.sema.types import BUILTINS


@dataclass
class ModeResult:
    """fn name -> 'own' | 'read' per parameter; includes builtins."""

    modes: dict[str, tuple[str, ...]] = field(default_factory=dict)


def infer_modes(
    module: ast.Module, resolved: ResolveResult, inferred: InferResult
) -> ModeResult:
    """Fixpoint over the call graph; never raises."""
    modes: dict[str, tuple[str, ...]] = {
        name: sig.modes for name, sig in BUILTINS.items()
    }
    # Section 28: a param that is ever assigned gets mode 'own' (even a
    # Copy-typed one). Unconditional, so it seeds the fixpoint init.
    assigned_vars = frozenset(resolved.assign_of.values())
    param_vars: dict[str, tuple[int | None, ...]] = {}
    for name, fn in resolved.fns.items():
        ids: list[int | None] = []
        for param in fn.params:
            bound = resolved.binds_of.get(param.node_id, ())
            ids.append(bound[0] if bound else None)
        param_vars[name] = tuple(ids)
        modes[name] = tuple(
            "own" if var_id is not None and var_id in assigned_vars else "read"
            for var_id in ids
        )

    changed = True
    while changed:
        changed = False
        for name, fn in resolved.fns.items():
            moved = cfg.move_used_vars(
                cfg.lower_fn(fn, resolved, inferred, modes)
            )
            current = modes[name]
            updated = tuple(
                "own"
                if current[i] == "own" or (var_id is not None and var_id in moved)
                else "read"
                for i, var_id in enumerate(param_vars[name])
            )
            if updated != current:
                modes[name] = updated
                changed = True
    return ModeResult(modes)
