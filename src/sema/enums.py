"""Enum, variant, match, and ``?`` typing for language v0.2/v0.2.1
(SPEC.md sections 28, 36).

Mixin consumed by ``src.sema.infer._Infer``. Covers variant construction
(payload variants as callees, nullary variants as bare values — OX0303
otherwise), match typing (scrutinee must solve to an enum; the full
OX0307 shape matrix: non-exhaustive, duplicate arm, arm from the wrong
enum, wrong binder arity, unreachable arm after ``_``, match on a
non-enum), arm-body unification into the match's type, and the ``?``
propagation operator (section 36): the operand must solve to
``Option<T>`` with the enclosing fn returning ``Option<U>``, or
``Result<T, E1>`` with the fn returning ``Result<U, E2>`` where E1
unifies with E2; the result type is T and any other shape is OX0308.

When the scrutinee's type is still unsolved, the target enum is
inferred from the first arm naming a known variant; a wildcard-only
match over an unsolved scrutinee defers its is-an-enum check until
after the global solve.

Only :mod:`src.sema.analyze`'s API is contractual; this module is an
internal helper of ``infer``.
"""

from __future__ import annotations

from src.diagnostics import Span
from src.parser import ast
from src.sema.types import (
    BUILTIN_ENUMS,
    BUILTIN_VARIANTS,
    ERROR_TYPE,
    TCon,
    TFn,
    TVar,
    Type,
    type_str,
)

_ERROR_NAME = ERROR_TYPE.name

# Sentinel target: a shape error was already reported (or the scrutinee is
# already-poisoned), so all further match shape checks are suppressed.
_SUPPRESS = object()


def _contains_error(ty: Type) -> bool:
    match ty:
        case TCon(name=name, args=args):
            if name == _ERROR_NAME:
                return True
            return any(_contains_error(a) for a in args)
        case TFn(params=params, ret=ret):
            return _contains_error(ret) or any(_contains_error(p) for p in params)
    return False


class _EnumOps:
    """Enum/variant/match methods mixed into ``_Infer``.

    Relies on the host class for unification (``unify``, ``_prune``,
    ``_resolve_full``, ``_instantiate``, ``_fresh``, ``_unsolved_roots``),
    traversal (``_expr``, ``_block``), diagnostics (``_diag``), and the
    ``resolved`` / ``var_tv`` / ``enum_variants`` / ``_cur_ret`` /
    ``_pending_matches`` / ``_pending_tries`` state it declares.
    """

    # ------------------------------------------------------------- registry

    def _is_enum_name(self, name: str) -> bool:
        return name in BUILTIN_ENUMS or name in self.resolved.enums

    def _variant_owner(self, vname: str) -> str | None:
        """Owning enum of a variant name, or None when unknown."""
        owner = BUILTIN_VARIANTS.get(vname)
        if owner is None:
            owner = self.resolved.variants.get(vname)
        return owner

    def _enum_variant_names(self, enum_name: str) -> tuple[str, ...]:
        sig = BUILTIN_ENUMS.get(enum_name)
        if sig is not None:
            return tuple(vname for vname, _payloads in sig.variants)
        return tuple(self.enum_variants.get(enum_name, ()))

    def _fresh_enum_type(self, enum_name: str) -> TCon:
        """The enum's type with fresh metavariable arguments (builtins) or
        no arguments (user enums are non-generic)."""
        sig = BUILTIN_ENUMS.get(enum_name)
        if sig is None:
            return TCon(enum_name)
        return TCon(enum_name, tuple(self._fresh() for _ in sig.generics))

    def _instantiate_variant(self, vname: str) -> tuple[TCon, tuple[Type, ...]]:
        """Fresh instantiation: (enum type, payload types) for ``vname``."""
        owner = self._variant_owner(vname)
        if owner is None:  # unreachable: resolve records only known variants
            return ERROR_TYPE, ()
        enum_ty = self._fresh_enum_type(owner)
        return enum_ty, self._payloads_in(vname, enum_ty)

    def _payloads_in(self, vname: str, target: TCon) -> tuple[Type, ...]:
        """Payload types of ``vname`` under the ``target`` instantiation."""
        sig = BUILTIN_ENUMS.get(target.name)
        if sig is None:
            return self.enum_variants.get(target.name, {}).get(vname, ())
        mapping = {
            generic.id: arg for generic, arg in zip(sig.generics, target.args)
        }
        payloads = dict(sig.variants)[vname]
        return tuple(self._instantiate(p, mapping) for p in payloads)

    # --------------------------------------------------------- variant uses

    def _variant_call(self, call: ast.Call, vname: str) -> Type:
        """A variant used as a callee (section 28: payload variants only,
        arity per payload; both violations are OX0303)."""
        enum_ty, payloads = self._instantiate_variant(vname)
        if not payloads:
            self._diag(
                "OX0303",
                f"nullary variant '{vname}' cannot be called; "
                "use it as a bare value",
                call.span,
            )
            for arg in call.args:
                self._expr(arg)
            return ERROR_TYPE
        if len(call.args) != len(payloads):
            self._diag(
                "OX0303",
                f"variant '{vname}' expects {len(payloads)} argument(s), "
                f"found {len(call.args)}",
                call.span,
            )
            for arg in call.args:
                self._expr(arg)
            return ERROR_TYPE
        for arg, payload_ty in zip(call.args, payloads):
            self.unify(self._expr(arg), payload_ty, arg.span)
        return enum_ty

    def _bare_variant(self, expr: ast.Var, vname: str) -> Type:
        """A variant used as a bare value (section 28: nullary only)."""
        enum_ty, payloads = self._instantiate_variant(vname)
        if payloads:
            self._diag(
                "OX0303",
                f"payload variant '{vname}' must be called with "
                f"{len(payloads)} argument(s)",
                expr.span,
            )
            return ERROR_TYPE
        return enum_ty

    # ---------------------------------------------------------- match typing

    def _match_expr(self, expr: ast.Match) -> Type:
        scrut_ty = self._expr(expr.scrutinee)
        target = self._match_target(expr, scrut_ty)
        shape_err = target is _SUPPRESS
        covered: set[str] = set()
        wildcard = False
        unreachable_reported = False
        result_ty: Type | None = None
        for arm in expr.arms:
            pat = arm.pattern
            binder_ids = self.resolved.binds_of.get(pat.node_id, ())
            if wildcard and not unreachable_reported and target is not _SUPPRESS:
                self._diag(
                    "OX0307", "unreachable match arm after wildcard '_'", pat.span
                )
                unreachable_reported = True
                shape_err = True
            if pat.name is None:
                wildcard = True
            elif isinstance(target, TCon):
                if not self._check_arm_variant(pat, binder_ids, target, covered):
                    shape_err = True
            else:
                # Suppressed, or deferred with no known variant anywhere
                # (a known one would have fixed the target).
                if target is None:
                    self._diag(
                        "OX0307", f"unknown variant '{pat.name}'", pat.span
                    )
                    shape_err = True
                self._poison_binders(binder_ids, pat.span)
            body_ty = self._arm_body(arm)
            if result_ty is None:
                result_ty = body_ty
            else:
                self.unify(result_ty, body_ty, arm.span)
        if isinstance(target, TCon) and not shape_err and not wildcard:
            missing = [
                vname
                for vname in self._enum_variant_names(target.name)
                if vname not in covered
            ]
            if missing:
                self._diag(
                    "OX0307",
                    "non-exhaustive match: missing "
                    + ", ".join(f"'{v}'" for v in missing),
                    expr.span,
                )
        if target is None and not shape_err:
            # Wildcard-only match over an unsolved scrutinee: check that it
            # solves to an enum after the global solve.
            self._pending_matches.append((scrut_ty, expr.scrutinee.span))
        return result_ty if result_ty is not None else ERROR_TYPE

    def _match_target(self, expr: ast.Match, scrut_ty: Type) -> object:
        """The enum the arms are checked against: a TCon, ``_SUPPRESS``
        (error reported / already-poisoned), or None (deferred)."""
        pruned = self._prune(scrut_ty)
        if isinstance(pruned, TCon):
            if pruned.name == _ERROR_NAME:
                return _SUPPRESS
            if self._is_enum_name(pruned.name):
                return pruned
            self._diag(
                "OX0307",
                "cannot match on non-enum type "
                f"{type_str(self._resolve_full(pruned))}",
                expr.scrutinee.span,
            )
            return _SUPPRESS
        # Unsolved scrutinee: infer the enum from the first arm that names
        # a known variant, and constrain the scrutinee to it.
        for arm in expr.arms:
            name = arm.pattern.name
            if name is None:
                continue
            owner = self._variant_owner(name)
            if owner is not None:
                enum_ty = self._fresh_enum_type(owner)
                self.unify(scrut_ty, enum_ty, expr.scrutinee.span)
                return enum_ty
        return None

    def _check_arm_variant(
        self,
        pat: ast.VariantPat,
        binder_ids: tuple[int, ...],
        target: TCon,
        covered: set[str],
    ) -> bool:
        """Shape-check one variant arm against the target enum; type its
        binders. Returns False when the arm reported OX0307. The caller
        guarantees ``pat.name`` is not the wildcard."""
        if pat.name is None:  # pragma: no cover — wildcard handled by caller
            return True
        owner = self._variant_owner(pat.name)
        if owner is None:
            self._diag("OX0307", f"unknown variant '{pat.name}'", pat.span)
            self._poison_binders(binder_ids, pat.span)
            return False
        if owner != target.name:
            self._diag(
                "OX0307",
                f"variant '{pat.name}' belongs to enum '{owner}', "
                f"not '{target.name}'",
                pat.span,
            )
            self._poison_binders(binder_ids, pat.span)
            return False
        duplicate = pat.name in covered
        if duplicate:
            self._diag(
                "OX0307",
                f"duplicate match arm for variant '{pat.name}'",
                pat.span,
            )
        covered.add(pat.name)
        payloads = self._payloads_in(pat.name, target)
        if len(binder_ids) != len(payloads):
            self._diag(
                "OX0307",
                f"variant '{pat.name}' has {len(payloads)} payload value(s); "
                f"the pattern binds {len(binder_ids)}",
                pat.span,
            )
            self._poison_binders(binder_ids, pat.span)
            return False
        for var_id, payload_ty in zip(binder_ids, payloads):
            self.unify(self.var_tv[var_id], payload_ty, pat.span)
        return not duplicate

    def _arm_body(self, arm: ast.MatchArm) -> Type:
        if isinstance(arm.body, ast.Block):
            return self._block(arm.body)
        return self._expr(arm.body)

    def _poison_binders(
        self, binder_ids: tuple[int, ...], span: Span
    ) -> None:
        """Bind an ill-shaped arm's binders to ERROR_TYPE so they do not
        cascade into OX0302/OX0300 reports."""
        for var_id in binder_ids:
            self.unify(self.var_tv[var_id], ERROR_TYPE, span)

    # ------------------------------------------------------------ '?' typing

    def _try_expr(self, expr: ast.Try) -> Type:
        """Type a ``?`` application (SPEC.md section 36). When the operand's
        head constructor is not yet known, the check is deferred until
        after the global solve (``_pending_tries``)."""
        op_ty = self._expr(expr.operand)
        result = self._try_result(op_ty, self._cur_ret, expr.span)
        if result is not None:
            return result
        tv = self._fresh()
        self._pending_tries.append((op_ty, self._cur_ret, expr.span, tv))
        return tv

    def _try_result(self, op_ty: Type, ret_ty: Type, span: Span) -> Type | None:
        """The result type of ``operand?`` — the payload T — or None to
        defer (operand head still unsolved). Shape violations are OX0308."""
        pruned = self._prune(op_ty)
        if isinstance(pruned, TVar):
            return None
        if isinstance(pruned, TCon):
            if pruned.name == _ERROR_NAME:
                return ERROR_TYPE
            if pruned.name == "Option" and len(pruned.args) == 1:
                return self._try_ret(ret_ty, "Option", None, pruned.args[0], span)
            if pruned.name == "Result" and len(pruned.args) == 2:
                return self._try_ret(
                    ret_ty, "Result", pruned.args[1], pruned.args[0], span
                )
        self._diag(
            "OX0308",
            "'?' requires an Option or Result operand, found "
            f"{type_str(self._resolve_full(pruned))}",
            span,
        )
        return ERROR_TYPE

    def _try_ret(
        self,
        ret_ty: Type,
        head: str,
        err_ty: Type | None,
        payload: Type,
        span: Span,
    ) -> Type:
        """Constrain the enclosing fn's return for one ``?`` (section 36):
        ``Option`` operand needs an Option-returning fn; ``Result<T, E1>``
        needs ``Result<U, E2>`` with E1 unifying with E2."""
        ret = self._prune(ret_ty)
        if isinstance(ret, TCon) and ret.name == _ERROR_NAME:
            return payload
        if isinstance(ret, TVar):
            # Unannotated return: '?' itself pins the head constructor.
            if head == "Option":
                self.unify(ret_ty, TCon("Option", (self._fresh(),)), span)
            else:
                self.unify(ret_ty, TCon("Result", (self._fresh(), err_ty)), span)
            return payload
        if isinstance(ret, TCon) and ret.name == head:
            if err_ty is not None:
                self.unify(err_ty, ret.args[1], span)
            return payload
        self._diag(
            "OX0308",
            f"'?' propagates {head} but the enclosing function returns "
            f"{type_str(self._resolve_full(ret))}",
            span,
        )
        return ERROR_TYPE

    def _flush_pending_tries(self) -> None:
        """Re-check deferred ``?`` applications as the solve fills in
        operand types; each resolution unifies the recorded result tv."""
        progress = True
        while progress and self._pending_tries:
            progress = False
            remaining: list[tuple[Type, Type, Span, Type]] = []
            for op_ty, ret_ty, span, result_tv in self._pending_tries:
                result = self._try_result(op_ty, ret_ty, span)
                if result is None:
                    remaining.append((op_ty, ret_ty, span, result_tv))
                else:
                    self.unify(result_tv, result, span)
                    progress = True
            self._pending_tries = remaining

    # ----------------------------------------------------- post-solve checks

    def _poison_leftover_deferred(self) -> None:
        """Bind the result tv of every still-deferred field access and
        ``?`` to ERROR_TYPE: their operand stayed unsolved, so OX0302 is
        reported at the operand's binding, not fabricated here."""
        leftover = [tv for _o, _f, _s, tv in self._pending_fields]
        leftover += [tv for _o, _r, _s, tv in self._pending_tries]
        for result_tv in leftover:
            pruned = self._prune(result_tv)
            if isinstance(pruned, TVar):
                self._subst[pruned.id] = ERROR_TYPE

    def _check_pending_matches(self) -> None:
        """Deferred is-an-enum checks for wildcard-only matches whose
        scrutinee type was unsolved during traversal."""
        for scrut_ty, span in self._pending_matches:
            if self._unsolved_roots(scrut_ty):
                continue  # ambiguous: OX0302 is reported elsewhere
            full = self._resolve_full(scrut_ty)
            if _contains_error(full):
                continue  # already-poisoned: suppressed
            if isinstance(full, TCon) and self._is_enum_name(full.name):
                continue
            self._diag(
                "OX0307",
                f"cannot match on non-enum type {type_str(full)}",
                span,
            )
