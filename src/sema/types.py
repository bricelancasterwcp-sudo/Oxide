"""Semantic type representations (SPEC.md sections 14-15, 28).

Defines the type constructors (``TVar``/``TCon``/``TFn``), the error type
that unifies with everything, the Copy predicate, the canonical type
printer, the builtin function signatures, and the builtin generic enums
``Option``/``Result`` with their reserved variant names (Part V).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TVar:
    """A type metavariable (unification variable)."""

    id: int


@dataclass(frozen=True, slots=True)
class TCon:
    """A type constructor application, e.g. ``Int`` or ``Vec<Int>``."""

    name: str
    args: tuple = ()


@dataclass(frozen=True, slots=True)
class TFn:
    """A function type. Functions are second-class; this appears only in
    top-level signatures, never as the type of an expression."""

    params: tuple
    ret: object


Type = TVar | TCon | TFn

# The internal error sentinel. Its name is deliberately unnameable (no
# identifier can contain '<'), so a user struct named 'Error' is an
# ordinary nominal type and can never alias the sentinel's
# unify-with-everything behavior. It still renders as 'Error'.
ERROR_TYPE = TCon("<error>")

INT = TCon("Int")
FLOAT = TCon("Float")
BOOL = TCon("Bool")
STR = TCon("Str")
UNIT = TCon("Unit")

_COPY_NAMES = frozenset({"Int", "Float", "Bool", "Unit", ERROR_TYPE.name})


def is_copy(ty: object) -> bool:
    """True for Copy types: Int, Float, Bool, Unit, and the Error type.

    Str, Vec, all user structs, metavariables, and function types are
    linear (non-copy).
    """
    return isinstance(ty, TCon) and ty.name in _COPY_NAMES


def type_str(ty: object) -> str:
    """Canonical rendering: 'Int', 'Vec<Int>', 'fn(Int, Str) -> Unit', '?'."""
    match ty:
        case TVar():
            return "?"
        case TCon(name="<error>", args=()):
            return "Error"
        case TCon(name=name, args=()):
            return name
        case TCon(name=name, args=args):
            return f"{name}<{', '.join(type_str(a) for a in args)}>"
        case TFn(params=params, ret=ret):
            joined = ", ".join(type_str(p) for p in params)
            return f"fn({joined}) -> {type_str(ret)}"
        case _:
            raise TypeError(f"type_str: not a type: {ty!r}")


@dataclass(frozen=True, slots=True)
class BuiltinSig:
    """Signature of a builtin function.

    ``generics`` lists the placeholder TVars that must be instantiated
    with fresh metavariables at every use site.
    """

    params: tuple
    ret: object
    modes: tuple[str, ...]
    generics: tuple


_A = TVar(-1)  # generic placeholders; instantiated fresh per use
_B = TVar(-2)

BUILTINS: dict[str, BuiltinSig] = {
    "print": BuiltinSig(params=(_A,), ret=UNIT, modes=("read",), generics=(_A,)),
    "len": BuiltinSig(
        params=(TCon("Vec", (_A,)),), ret=INT, modes=("read",), generics=(_A,)
    ),
    "push": BuiltinSig(
        params=(TCon("Vec", (_A,)), _A),
        ret=TCon("Vec", (_A,)),
        modes=("own", "own"),
        generics=(_A,),
    ),
    "vec": BuiltinSig(params=(), ret=TCon("Vec", (_A,)), modes=(), generics=(_A,)),
    # ---- v0.2 builtins (SPEC.md section 28), modes pinned ----
    "clone": BuiltinSig(params=(_A,), ret=_A, modes=("read",), generics=(_A,)),
    "get": BuiltinSig(
        params=(TCon("Vec", (_A,)), INT),
        ret=TCon("Option", (_A,)),
        modes=("read", "read"),
        generics=(_A,),
    ),
    "range": BuiltinSig(
        params=(INT, INT), ret=TCon("Vec", (INT,)), modes=("read", "read"), generics=()
    ),
    "print_str": BuiltinSig(params=(STR,), ret=UNIT, modes=("read",), generics=()),
    "str_len": BuiltinSig(params=(STR,), ret=INT, modes=("read",), generics=()),
    "concat": BuiltinSig(params=(STR, STR), ret=STR, modes=("own", "own"), generics=()),
    "chars": BuiltinSig(
        params=(STR,), ret=TCon("Vec", (STR,)), modes=("read",), generics=()
    ),
    "int_to_str": BuiltinSig(params=(INT,), ret=STR, modes=("read",), generics=()),
    "parse_int": BuiltinSig(
        params=(STR,), ret=TCon("Option", (INT,)), modes=("read",), generics=()
    ),
    # ---- v0.2.1 builtins (SPEC.md section 36), modes pinned ----
    "to_float": BuiltinSig(params=(INT,), ret=FLOAT, modes=("read",), generics=()),
    "trunc": BuiltinSig(params=(FLOAT,), ret=INT, modes=("read",), generics=()),
    # ---- v0.3 builtins (SPEC.md section 57), modes pinned ----
    # An ALIAS of int_to_str, which stays. Models reach for the shorter
    # spelling untaught -- 85.7% of observed to_str sites are plain calls,
    # and 6 programs defined `fn to_str` themselves rather than use the
    # longer name. Int only: the language has no overloading.
    "to_str": BuiltinSig(params=(INT,), ret=STR, modes=("read",), generics=()),
}


@dataclass(frozen=True, slots=True)
class BuiltinEnum:
    """A builtin generic enum (SPEC.md section 28): ``Option``/``Result``.

    ``generics`` are placeholder TVars instantiated fresh (or mapped to a
    known instantiation's arguments) at every variant use; ``variants``
    pairs each variant name with its payload types over those placeholders.
    """

    name: str
    generics: tuple
    variants: tuple[tuple[str, tuple], ...]


BUILTIN_ENUMS: dict[str, BuiltinEnum] = {
    "Option": BuiltinEnum("Option", (_A,), (("Some", (_A,)), ("None", ()))),
    "Result": BuiltinEnum("Result", (_A, _B), (("Ok", (_A,)), ("Err", (_B,)))),
}

# Reserved variant names (user redefinition anywhere at top level -> OX0203),
# mapping each to its owning builtin enum.
BUILTIN_VARIANTS: dict[str, str] = {
    "Some": "Option",
    "None": "Option",
    "Ok": "Result",
    "Err": "Result",
}
