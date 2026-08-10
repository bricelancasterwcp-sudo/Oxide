"""Demand counters for the generation-friction taxonomy.

Two pinned signals over model output:

* ``builtin_self_definitions`` -- the model writes ``fn NAME`` for a NAME
  the language already has. A model defining a function is a language
  telling you it lacks a name its users want, and it is a stronger signal
  than a failed call because the model paid to work around the gap.
* ``unresolved_calls`` -- plain-call sites for names the language does NOT
  have. This is the counter that must move to zero when a name is added.

MEASUREMENT RULE, and the reason this module exists rather than a regex in
a session that ends: count DISTINCT PROGRAMS, not raw occurrences. On the
G0 corpus, occurrence counts give qwen ``to_int`` = 291 and granite
``to_vec`` = 337; both collapse to ONE program under program counting --
the degenerate whole-program repetition the taxonomy discounts elsewhere.
Per-program counts are reported alongside every occurrence count so the
two can never be confused again.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

from src.sema.types import BUILTINS

_IDENT = r"[A-Za-z_][A-Za-z_0-9]*"
_DEF = re.compile(rf"\bfn\s+({_IDENT})\s*\(")
# A plain call: NAME( not preceded by a dot (receiver form) and not by `fn `.
_CALL = re.compile(rf"(?<![.\w])({_IDENT})\s*\(")


def builtin_self_definitions(source: str) -> collections.Counter:
    """``fn NAME`` definitions where NAME is already a builtin."""
    found = collections.Counter()
    for name in _DEF.findall(source):
        if name in BUILTINS:
            found[name] += 1
    return found


def unresolved_calls(source: str, names: tuple[str, ...]) -> collections.Counter:
    """Plain-call sites for each of *names* that the language does NOT have.

    A name present in ``BUILTINS`` scores 0 by construction: once it
    resolves it is no longer unresolved, which is exactly the endpoint a
    builtin addition is meant to move.
    """
    defined = set(_DEF.findall(source))
    found = collections.Counter({n: 0 for n in names})
    for name in _CALL.findall(source):
        if name in names and name not in BUILTINS and name not in defined:
            found[name] += 1
    return found


def scan_oxide_arm(root: Path) -> dict:
    """Aggregate both counters over a run root's oxide-arm first attempts.

    Reports occurrences AND the number of distinct programs carrying each
    signal, because the two differ by two orders of magnitude on this
    corpus and only the second is interpretable.
    """
    occ = collections.Counter()
    progs = collections.Counter()
    total = 0
    for raw in sorted(Path(root).glob("*/raw/*.oxide.1.txt")):
        total += 1
        text = raw.read_text(encoding="utf-8", errors="replace")
        defs = builtin_self_definitions(text)
        occ.update(defs)
        for name in defs:
            progs[name] += 1
    return {
        "programs": total,
        "self_definitions": occ,
        "self_definition_programs": progs,
    }
