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

DELIBERATELY TEXTUAL, NOT PARSE-BASED. These counters match against raw
source text with regexes; they do not call the parser. This is not an
oversight -- parse-gating would destroy the signal this module exists to
measure. Of the 6 programs carrying ``fn to_str`` in the G0 corpus, only
ONE parses cleanly: a model that defines a missing builtin is a model
already in trouble, so the programs carrying this signal are
overwhelmingly the ones that fail to parse. Gating on a successful parse
would report 1 definition in 1 program instead of 15 across 6 --
systematically discarding exactly the population the counter exists to
measure. (Corpus-wide the parse rate is 541/600 = 90.2%; on the
signal-carrying subset it is 1/6.) Contrast with ``eval/deformation.py``,
which does parse: its signature is a syntactic structure -- an
expression-statement whose expression is a ``==`` BinOp with a
FieldAccess LHS -- that only exists in a parsed AST. A bare name is not.

KNOWN LIMITATION. Because matching is textual, occurrences inside
comments and string literals are counted the same as genuine source use
-- ``fn to_str`` written in a comment, or the text ``to_int(`` sitting
inside a string literal, both match. The counts these functions return
are therefore upper bounds on genuine source occurrences, not exact
counts.
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

# The pinned watched set for corpus-scale unresolved-call aggregation.
# `to_str` is included even though it now resolves (see BUILTINS): its
# unresolved-call count is 0 by construction, and that zero IS the g3
# endpoint -- "to_str-shaped failures -> 0" is only auditable if the name
# stays in the watched set across the change that resolved it.
WATCHED_NAMES: tuple[str, ...] = ("to_str", "to_int", "to_string")


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


def scan_oxide_arm(root: Path, names: tuple[str, ...] = WATCHED_NAMES) -> dict:
    """Aggregate both counters over a run root's oxide-arm first attempts.

    Reports occurrences AND the number of distinct programs carrying each
    signal, for both ``builtin_self_definitions`` and ``unresolved_calls``,
    because the two differ by two orders of magnitude on this corpus and
    only the program count is interpretable. *names* is the watched set
    for the unresolved-call side; it defaults to ``WATCHED_NAMES``.
    """
    occ = collections.Counter()
    progs = collections.Counter()
    unresolved_occ = collections.Counter()
    unresolved_progs = collections.Counter()
    total = 0
    for raw in sorted(Path(root).glob("*/raw/*.oxide.1.txt")):
        total += 1
        text = raw.read_text(encoding="utf-8", errors="replace")

        defs = builtin_self_definitions(text)
        occ.update(defs)
        for name in defs:
            progs[name] += 1

        calls = unresolved_calls(text, names)
        for name, count in calls.items():
            if count:
                unresolved_occ[name] += count
                unresolved_progs[name] += 1

    return {
        "programs": total,
        "self_definitions": occ,
        "self_definition_programs": progs,
        "unresolved_calls": unresolved_occ,
        "unresolved_call_programs": unresolved_progs,
    }
