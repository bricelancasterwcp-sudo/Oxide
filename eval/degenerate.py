"""Taxonomy of degenerate repairs (ownership probe follow-up).

A *degenerate* repair silences the ownership diagnostic while changing what
the program does: `lenient` passes, `strict` fails. Across three model
families it runs 38-68% in the Oxide arms against 3-7% for Rust -- an
order-of-magnitude gap that survived every increase in data and is the
largest signal this instrument produces. Nothing in the scores says *how*
the programs were broken, which is what this module classifies.

The categories are fixed here deliberately, before looking at the data, so
they are not fitted to what turns up. Each is a distinct hypothesis about
what the model was doing:

``clone``
    A ``clone``/``.clone()`` was introduced. The model took the escape the
    diagnostic offers. For an accumulator this silently stops the value
    growing -- the confirmed OX0403 failure mode.
``deleted-use``
    A use present in the broken program is gone. The model removed the
    conflict by removing the work.
``early-drop``
    A ``drop`` was added or moved (explicit arm). The model ended the
    value's life early rather than fixing the flow.
``reordered``
    Same statements, different order. The model moved the read before the
    move -- often legitimate, so a strict failure here means it changed
    semantics while doing so.
``rewrote``
    Structurally different program. The model restarted rather than
    repaired.
``other``
    None of the above matched; inspect by hand.

A repair may match several; ``classify`` returns every category that fits,
because "cloned AND deleted a use" is a different behaviour from either
alone and collapsing them would hide it.
"""

from __future__ import annotations

import re
from collections import Counter

#: Order is presentation only; classification is not exclusive.
CATEGORIES = (
    "clone",
    "deleted-use",
    "early-drop",
    "reordered",
    "rewrote",
    "other",
)

_CLONE = re.compile(r"\bclone\b")
_DROP = re.compile(r"^\s*drop\s+\w+\s*$", re.MULTILINE)
_IDENT_CALL = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(")


def _norm(source: str) -> list[str]:
    """Statement-ish lines, stripped and blank-free."""
    return [ln.strip() for ln in source.split("\n") if ln.strip()]


def _counts(source: str) -> Counter[str]:
    return Counter(_IDENT_CALL.findall(source))


def classify(broken: str, fix: str, submitted: str) -> list[str]:
    """Every category the submitted repair matches.

    ``broken`` and ``fix`` are the probe's own two halves; ``submitted`` is
    what the model returned. Comparing against BOTH matters: a category
    like ``clone`` is only interesting when the reference fix does not
    itself clone, otherwise cloning is simply the right answer.
    """
    out: list[str] = []
    sub_lines, broken_lines = _norm(submitted), _norm(broken)

    if _CLONE.search(submitted) and not _CLONE.search(fix):
        out.append("clone")

    sub_calls, broken_calls = _counts(submitted), _counts(broken)
    if any(sub_calls[name] < count for name, count in broken_calls.items()):
        out.append("deleted-use")

    if len(_DROP.findall(submitted)) != len(_DROP.findall(broken)):
        out.append("early-drop")

    if sorted(sub_lines) == sorted(broken_lines) and sub_lines != broken_lines:
        out.append("reordered")

    kept = len(set(sub_lines) & set(broken_lines))
    if broken_lines and kept / len(broken_lines) < 0.5:
        out.append("rewrote")

    return out or ["other"]


def summarize(results: list[dict], probes: dict[tuple[str, str], dict]) -> dict:
    """Category counts over the degenerate repairs in ``results``.

    ``probes`` maps ``(id, arm)`` to the corpus record. Only repairs that
    are lenient-pass and strict-fail are counted -- the definition of
    degenerate.
    """
    per_arm: dict[str, Counter[str]] = {}
    totals: dict[str, int] = {}
    for row in results:
        if not (row["lenient"] and not row["strict"]):
            continue
        record = probes.get((row["id"], row["arm"]))
        if record is None or "source" not in row:
            continue
        arm = row["arm"]
        totals[arm] = totals.get(arm, 0) + 1
        bucket = per_arm.setdefault(arm, Counter())
        for label in classify(record["broken"], record["fix"], row["source"]):
            bucket[label] += 1
    return {
        "degenerate_totals": totals,
        "categories": {a: dict(c.most_common()) for a, c in per_arm.items()},
    }
