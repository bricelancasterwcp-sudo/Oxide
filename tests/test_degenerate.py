"""Tests for the degenerate-repair taxonomy (eval/degenerate.py).

A degenerate repair silences the ownership diagnostic while changing what
the program does. The classifier's job is to say HOW. These tests pin it
against cases whose correct label is known independently -- above all the
real frontier failure that motivated the whole investigation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import degenerate  # noqa: E402

BROKEN = (
    "fn main() {\n"
    "    let acc = range(0, 10)\n"
    "    for i in range(0, 3) {\n"
    "        let grown = push(acc, i)\n"
    "    }\n"
    "    print(len(acc))\n"
    "}\n"
)
FIX = BROKEN.replace("let grown = push(acc, i)", "acc = push(acc, i)")


def test_the_real_frontier_failure_is_classified_as_clone():
    """The case this investigation exists for. A frontier model, given the
    old OX0403 diagnostic, wrote exactly this in both Oxide dialects: it
    compiles, silences the error, and never accumulates."""
    submitted = BROKEN.replace(
        "push(acc, i)", "push(clone(acc), i)"
    )
    assert "clone" in degenerate.classify(BROKEN, FIX, submitted)


def test_the_correct_fix_matches_no_degenerate_category():
    """The classifier must not label the right answer. If it did, every
    rate it produced would be inflated by the successes."""
    assert degenerate.classify(BROKEN, FIX, FIX) == ["other"]


def test_removing_the_conflicting_use_is_deleted_use():
    submitted = BROKEN.replace("        let grown = push(acc, i)\n", "")
    assert "deleted-use" in degenerate.classify(BROKEN, FIX, submitted)


def test_wholesale_rewrite_is_flagged():
    assert "rewrote" in degenerate.classify(
        BROKEN, FIX, "fn main() {\n    print(13)\n}\n"
    )


def test_cloning_is_not_flagged_when_the_reference_fix_also_clones():
    """`clone` is only a degenerate signal where cloning is the wrong
    answer. On a probe whose own fix clones, it is simply correct."""
    clone_fix = BROKEN.replace("push(acc, i)", "push(clone(acc), i)")
    assert "clone" not in degenerate.classify(BROKEN, clone_fix, clone_fix)


def test_explicit_arm_drop_changes_are_early_drop():
    broken = (
        "fn main() {\n"
        "    let v = push(vec(), 1)\n"
        "    print(len(&v))\n"
        "    drop v\n"
        "}\n"
    )
    submitted = broken.replace("    drop v\n", "")
    assert "early-drop" in degenerate.classify(broken, broken, submitted)


def test_classification_is_not_exclusive():
    """A repair that both clones and drops a use is a different behaviour
    from either alone; collapsing them would hide it."""
    submitted = (
        "fn main() {\n"
        "    let acc = range(0, 10)\n"
        "    let x = clone(acc)\n"
        "}\n"
    )
    labels = degenerate.classify(BROKEN, FIX, submitted)
    assert len(labels) > 1, labels


def test_summarize_counts_only_degenerate_rows():
    probes = {("p17", "oxide"): {"broken": BROKEN, "fix": FIX}}
    rows = [
        # degenerate: lenient pass, strict fail
        {"id": "p17", "arm": "oxide", "lenient": True, "strict": False,
         "source": BROKEN.replace("push(acc, i)", "push(clone(acc), i)")},
        # a success -- must not be counted
        {"id": "p17", "arm": "oxide", "lenient": True, "strict": True,
         "source": FIX},
        # a hard failure -- must not be counted
        {"id": "p17", "arm": "oxide", "lenient": False, "strict": False,
         "source": "garbage"},
    ]
    out = degenerate.summarize(rows, probes)
    assert out["degenerate_totals"] == {"oxide": 1}
    assert out["categories"]["oxide"]["clone"] == 1
