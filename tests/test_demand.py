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


def test_unparseable_source_does_not_raise():
    assert builtin_self_definitions("&&& not a program") == {}


@pytest.mark.skipif(not G0.is_dir(), reason="G0 corpus absent")
def test_reproduces_the_g0_to_str_baseline():
    """The design's pre-change numbers, pinned so the endpoint is auditable.
    parse_source is live, so a future parser change could move these with
    nothing else failing."""
    got = scan_oxide_arm(G0)
    assert got["programs"] == 600
    assert got["self_definitions"]["to_str"] == 15
    assert got["self_definition_programs"]["to_str"] == 6
