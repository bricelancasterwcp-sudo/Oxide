"""Demand counters, with pinned definitions (SPEC §57, g3 design).

A model that writes `fn to_str` itself is telling you the language lacks a
name its users want. That signal is what these counters measure, and it
must be reproducible in a year without this conversation.
"""

from pathlib import Path

import pytest

from eval.demand import builtin_self_definitions, scan_oxide_arm, unresolved_calls

G0 = Path("eval/results/g0-generation-baseline/constrained")


def test_self_definition_of_a_builtin_is_counted():
    src = "fn to_str(n: Int) -> Str { int_to_str(n) }\nfn main() { }"
    assert builtin_self_definitions(src)["to_str"] == 1


def test_a_non_builtin_definition_is_not_counted():
    """Only shadowing a BUILTIN is the signal. Ordinary user functions are
    not evidence of a missing name."""
    src = "fn helper(n: Int) -> Int { n }\nfn main() { }"
    assert builtin_self_definitions(src) == {}


def test_unresolved_calls_ignore_names_that_exist():
    """to_str now resolves, so it must NOT be reported as unresolved --
    this is the g3 endpoint and it has to move when the alias lands."""
    src = "fn main() { print_str(to_str(1)) }"
    assert unresolved_calls(src, ("to_str", "to_int"))["to_str"] == 0


def test_unresolved_calls_count_names_that_do_not_exist():
    src = "fn main() { print_str(to_int(1)) }"
    assert unresolved_calls(src, ("to_str", "to_int"))["to_int"] == 1


def test_a_definition_is_not_also_counted_as_a_call():
    """`fn to_int(...)` is a definition, not a call site. Conflating them
    double-counts the very signal the module is built to separate."""
    src = "fn to_int(s: Str) -> Int { 0 }\nfn main() { }"
    assert unresolved_calls(src, ("to_int",))["to_int"] == 0


def test_malformed_source_without_an_fn_pattern_yields_no_counts():
    """The counters are textual, not syntactic (see eval/demand.py's module
    docstring): a pure regex function cannot raise a parse error, so "does
    not raise on unparseable input" was a vacuous claim -- it would hold
    for any input to any pure function. What is real is what the regex
    does: with no `fn NAME(` substring present, no count is produced."""
    assert builtin_self_definitions("&&& not a program") == {}


def test_malformed_source_with_an_fn_pattern_still_matches_textually():
    """Known limitation (see eval/demand.py's module docstring): because
    matching is textual rather than syntactic, a `fn to_str(` substring is
    counted even inside a fragment that could never parse as a program.
    This is an upper bound on genuine source occurrences, not an exact
    count -- demonstrated honestly here rather than asserted away."""
    garbage = "&&& fn to_str( this is not valid syntax {{{ ]"
    assert builtin_self_definitions(garbage)["to_str"] == 1


def test_scan_oxide_arm_aggregates_unresolved_calls_with_program_counts(tmp_path):
    """unresolved_calls needs a corpus-scale path with the same
    occurrence/program pairing discipline builtin_self_definitions already
    has -- that pairing is this module's entire reason for existing. Three
    call sites in one program plus one call site in a second program:
    4 occurrences, but only 2 programs."""
    fam_a = tmp_path / "fam-a" / "raw"
    fam_a.mkdir(parents=True)
    (fam_a / "s1.oxide.1.txt").write_text(
        "fn main() { to_int(1) to_int(2) to_int(3) }"
    )
    fam_b = tmp_path / "fam-b" / "raw"
    fam_b.mkdir(parents=True)
    (fam_b / "s1.oxide.1.txt").write_text("fn main() { to_int(9) }")

    got = scan_oxide_arm(tmp_path, names=("to_int",))

    assert got["unresolved_calls"]["to_int"] == 4
    assert got["unresolved_call_programs"]["to_int"] == 2


@pytest.mark.skipif(not G0.is_dir(), reason="G0 corpus absent")
def test_reproduces_the_g0_to_str_baseline():
    """The design's pre-change numbers, pinned so the endpoint is
    auditable. These counters are textual, not parse-based (see
    eval/demand.py's module docstring for why); the pin is stable because
    the corpus text itself is fixed, not because a live parser backs it."""
    got = scan_oxide_arm(G0)
    assert got["programs"] == 600
    assert got["self_definitions"]["to_str"] == 15
    assert got["self_definition_programs"]["to_str"] == 6
