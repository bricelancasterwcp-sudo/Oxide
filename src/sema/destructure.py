"""Struct destructuring-pattern typing for language v0.2 (SPEC.md
sections 14, 27).

Mixin consumed by ``src.sema.infer._Infer``. This is one half of
``_let``'s typing: ``infer._let`` keeps the ``BindPat`` case and
delegates the ``DestructPat`` case here. The two halves share only
``_let``'s entry point -- this one touches no other statement form --
which is what made the split a safe way to bring ``infer.py`` back under
the project's 800-line cap when the section-56 field-assignment work
pushed it over. The extracted code is byte-identical to the original.

Only :mod:`src.sema.analyze`'s API is contractual; this module is an
internal helper of ``infer``.
"""

from __future__ import annotations

from src.diagnostics import Span
from src.parser import ast
from src.sema.types import ERROR_TYPE, TCon, Type


class _DestructOps:
    """`let S { a, b } = e` pattern typing, mixed into ``_Infer``.

    Relies on the host class for unification (``unify``), diagnostics
    (``_diag``), and the ``resolved`` / ``var_tv`` / ``struct_fields``
    state it declares.
    """

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
