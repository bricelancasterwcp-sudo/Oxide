"""Blind tests for the Phase 3 linear checker (SPEC.md Part III, §20 second file).

Covers the §19 goldens S1-S6, S11, S12 exactly as stated, plus the remaining
§20 items: move/read classification, poisoning, if/else drop hoisting, loop
semantics, the Copy exemption, shadowing chains, field-access-as-implicit-
clone (v0.2.1, SPEC.md section 36 — OX0405 is retired), and the infer-error
gate that suppresses linear analysis and drops.

Imports only the blind-test surface from ``src.sema.analyze`` (plus pytest).
"""

import pytest

from src.sema.analyze import (
    analyze,
    diag_codes,
    drop_list,
    param_modes,
    use_classes,
    var_types_by_name,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def codes_of(source: str) -> list[str]:
    """Analyze source and return the full phase-ordered diagnostic code list."""
    return diag_codes(analyze(source))


def diags_with_code(res: object, code: str) -> list[object]:
    """Diagnostics from a SemaResult filtered to one code."""
    return [d for d in res.diagnostics if d.code == code]


# ---------------------------------------------------------------------------
# §19 golden sources (inline "\n" in the spec = real newlines in the source)
# ---------------------------------------------------------------------------

S1_SRC = "fn main() { let v = vec()\n let v2 = push(v, 1)\n print(len(v2)) }"
S2_SRC = "fn main() { let v = vec()\n let w = push(v, 1)\n print(len(v)) }"
S3_SRC = (
    "fn f(v: Vec<Int>) -> Vec<Int> { let a = push(v, 1)\n let b = push(v, 2)\n a }"
)
S4_SRC = "fn g(c: Bool, v: Vec<Int>) { if c { let w = push(v, 1) } }"
S5_SRC = "fn h(v: Vec<Int>) { while true { let w = push(v, 1) } }"
S6_SRC = "fn k(c: Bool, v: Vec<Int>) -> Int { if c { return 0 }\n len(v) }"
S11_SRC = "fn h3(v: Vec<Int>) { while true { print(len(v)) } }"
S12_SRC = "fn t(v: Vec<Int>) { push(v, 1)\n print(0) }"


# ---------------------------------------------------------------------------
# Item 1 — goldens S1-S6, S11, S12 (§19, asserted exactly)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected_codes", "expected_drops"),
    [
        pytest.param(S1_SRC, [], [("main", "v2", "after-stmt")], id="s1-clean-chain"),
        pytest.param(S2_SRC, ["OX0400"], [], id="s2-use-after-move"),
        pytest.param(S3_SRC, ["OX0401"], [], id="s3-double-move"),
        pytest.param(
            S4_SRC,
            [],
            [("g", "v", "branch-end"), ("g", "w", "block-end")],
            id="s4-absent-else-hoist",
        ),
        pytest.param(S5_SRC, ["OX0403"], [], id="s5-moved-in-loop"),
        pytest.param(
            S6_SRC,
            [],
            [],
            id="s6-early-return",
        ),
        pytest.param(S11_SRC, [], [], id="s11-loop-read"),
        pytest.param(S12_SRC, [], [("t", "<temp>", "after-stmt")], id="s12-temp"),
    ],
)
def test_golden_diag_codes_and_drop_list(source, expected_codes, expected_drops):
    # Act
    res = analyze(source)
    # Assert
    assert diag_codes(res) == expected_codes
    assert drop_list(res) == expected_drops


def test_s1_both_vec_bindings_infer_vec_int():
    # Act
    res = analyze(S1_SRC)
    # Assert
    assert var_types_by_name(res, "main", "v") == ["Vec<Int>"]
    assert var_types_by_name(res, "main", "v2") == ["Vec<Int>"]


def test_s1_use_classes_are_move_for_v_and_read_for_v2():
    # Act
    res = analyze(S1_SRC)
    # Assert
    assert use_classes(res, "main", "v") == ["move"]
    assert use_classes(res, "main", "v2") == ["read"]


def test_s2_use_after_move_diagnostic_carries_note_on_the_move():
    # Act
    res = analyze(S2_SRC)
    # Assert
    reports = diags_with_code(res, "OX0400")
    assert len(reports) == 1
    assert len(reports[0].notes) >= 1


def test_s3_double_moved_param_infers_own_mode():
    # Act
    res = analyze(S3_SRC)
    # Assert
    assert param_modes(res, "f") == ("own",)


def test_s4_param_modes_are_read_for_copy_and_own_for_moved():
    # Act
    res = analyze(S4_SRC)
    # Assert
    assert param_modes(res, "g") == ("read", "own")


def test_return_after_last_use_does_not_double_drop():
    # Arrange — v is a read-mode non-copy param, hence a caller-owned
    # borrow (amended section 18): the callee synthesizes no drops for it
    # on either the fallthrough or the early-return path
    source = "fn f(c: Bool, v: Vec<Int>) { print(len(v))\nif c { return }\nprint(0) }"
    multi = (
        "fn f(c: Bool, d: Bool, v: Vec<Int>) "
        "{ print(len(v))\nif c { return }\nif d { return }\nprint(0) }"
    )
    # Act
    res = analyze(source)
    # Assert — no callee drops of v at all (caller-owned)
    assert diag_codes(res) == []
    assert drop_list(res) == []
    assert drop_list(analyze(multi)) == []


# ---------------------------------------------------------------------------
# Item 2 — let-initializer move, then use of the source vs the destination
# ---------------------------------------------------------------------------


def test_use_of_source_after_let_move_reports_use_after_move_with_notes():
    # Arrange
    source = "fn f(x: Vec<Int>) { let y = x\n print(len(x)) }"
    # Act
    res = analyze(source)
    # Assert
    assert diag_codes(res) == ["OX0400"]
    reports = diags_with_code(res, "OX0400")
    assert len(reports) == 1
    assert len(reports[0].notes) >= 1


def test_use_of_destination_after_let_move_is_clean():
    # Arrange
    source = "fn f(x: Vec<Int>) { let y = x\n print(len(y)) }"
    # Act / Assert
    assert codes_of(source) == []


# ---------------------------------------------------------------------------
# Item 3 — poisoning: one diagnostic per variable per function
# ---------------------------------------------------------------------------


def test_two_uses_after_move_report_exactly_one_diagnostic():
    # Arrange
    source = "fn f(x: Vec<Int>) { let y = x\n print(len(x))\n print(len(x)) }"
    # Act / Assert: poisoned after the first report, so exactly one code total
    assert codes_of(source) == ["OX0400"]


# ---------------------------------------------------------------------------
# Item 4 — real-else hoisting: still-owning arm drops at its block end
# ---------------------------------------------------------------------------


def test_real_else_hoists_drop_into_still_owning_arm():
    # Arrange
    source = "fn g(c: Bool, v: Vec<Int>) { if c { let a = push(v, 1) } else { } }"
    # Act
    res = analyze(source)
    # Assert
    assert diag_codes(res) == []
    assert drop_list(res) == [("g", "a", "block-end"), ("g", "v", "block-end")]


# ---------------------------------------------------------------------------
# Item 5 — conditional move then a later use
# ---------------------------------------------------------------------------


def test_move_in_one_arm_then_use_after_merge_reports_use_after_move():
    # Arrange
    source = "fn g(c: Bool, v: Vec<Int>) { if c { let a = push(v, 1) }\n print(len(v)) }"
    # Act
    res = analyze(source)
    # Assert
    assert diag_codes(res) == ["OX0400"]
    assert drop_list(res) == []


# ---------------------------------------------------------------------------
# Item 6 — both arms consume: no merge drop for the moved var, no codes
# ---------------------------------------------------------------------------


def test_both_arms_consuming_leaves_no_drop_for_the_moved_var():
    # Arrange
    source = (
        "fn g(c: Bool, v: Vec<Int>) "
        "{ if c { let a = push(v, 1) } else { let b = push(v, 2) } }"
    )
    # Act
    res = analyze(source)
    drops = drop_list(res)
    # Assert
    assert diag_codes(res) == []
    assert all(var_name != "v" for _fn, var_name, _kind in drops)
    assert drops == [("g", "a", "block-end"), ("g", "b", "block-end")]


# ---------------------------------------------------------------------------
# Item 7 — a binding local to the loop body is clean
# ---------------------------------------------------------------------------


def test_loop_local_binding_drops_at_block_end_without_codes():
    # Arrange
    source = "fn f() { while true { let w = push(vec(), 1) } }"
    # Act
    res = analyze(source)
    # Assert
    assert diag_codes(res) == []
    assert drop_list(res) == [("f", "w", "block-end")]


# ---------------------------------------------------------------------------
# Item 8 — Copy exemption: Int locals are always 'copy', never dropped
# ---------------------------------------------------------------------------


def test_copy_var_used_three_times_classifies_copy_with_no_drops():
    # Arrange
    source = "fn f() { let x = 1\n print(x)\n print(x)\n print(x) }"
    # Act
    res = analyze(source)
    # Assert
    assert diag_codes(res) == []
    assert use_classes(res, "f", "x") == ["copy", "copy", "copy"]
    assert drop_list(res) == []


# ---------------------------------------------------------------------------
# Item 9 — shadowing chain: move into the shadow, read of the shadow
# ---------------------------------------------------------------------------


def test_shadowing_chain_is_clean_with_one_after_stmt_drop():
    # Arrange
    source = "fn f() { let v = push(vec(), 1)\n let v = push(v, 2)\n print(len(v)) }"
    # Act
    res = analyze(source)
    # Assert
    assert diag_codes(res) == []
    assert use_classes(res, "f", "v") == ["move", "read"]
    assert drop_list(res) == [("f", "v", "after-stmt")]


# ---------------------------------------------------------------------------
# Item 10 — unused linear param: caller-owned read borrow, no callee drops
# ---------------------------------------------------------------------------


def test_unused_linear_param_has_no_drops_and_read_mode():
    # Arrange
    source = "fn f(v: Vec<Int>) { }"
    # Act
    res = analyze(source)
    # Assert — caller-owned read borrow (amended section 18): no drops
    assert diag_codes(res) == []
    assert param_modes(res, "f") == ("read",)
    assert drop_list(res) == []


# ---------------------------------------------------------------------------
# Item 11 — field access is an implicit clone (v0.2.1, SPEC.md section 36;
# supersedes the retired OX0405)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected_drops"),
    [
        # The clone is a temporary consumed by the call (out of DropPoint
        # scope, like match unbound payloads); `s` has only a read use, so
        # it stays a caller-owned read-mode param with no callee drops.
        pytest.param("print(len(s.v))", [], id="read-position"),
        # The clone is bound to `x` and never used again: block-end drop
        # per section 18. `s` itself stays owned (base use is a read).
        pytest.param(
            "let x = s.v", [("f", "x", "block-end")], id="move-position"
        ),
    ],
)
def test_non_copy_field_access_is_an_implicit_clone(body, expected_drops):
    # Arrange — section 36: `s.f` is legal for every field type; the value
    # is a fresh owned clone and the base var stays owned.
    source = "struct S { v: Vec<Int> }\nfn f(s: S) { " + body + " }"
    # Act
    res = analyze(source)
    # Assert
    assert diag_codes(res) == []
    assert param_modes(res, "f") == ("read",)
    assert drop_list(res) == expected_drops


# ---------------------------------------------------------------------------
# Item 12 — infer-error gate: no linear codes, no drops
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("fn bad() { let x = 1 + true }", id="simple-type-error"),
        pytest.param(
            "fn bad(v: Vec<Int>) "
            "{ let a = push(v, 1)\n let b = push(v, 2)\n let x = 1 + true }",
            id="type-error-suppresses-would-be-double-move",
        ),
    ],
)
def test_type_error_source_suppresses_linear_codes_and_drops(source):
    # Act
    res = analyze(source)
    codes = diag_codes(res)
    # Assert
    assert "OX0300" in codes
    assert not any(code.startswith("OX04") for code in codes)
    assert drop_list(res) == []


# ---------------------------------------------------------------------------
# Regressions — SPEC.md section 18 exactly-once (DropPoint contract)
# ---------------------------------------------------------------------------


def test_reinit_then_move_loop_drops_pre_loop_value_on_skip_edge():
    """Regression: a loop body whose net effect is reinit-before-move
    (``v2 = vec()`` then ``consume(v2)``) is conflict-free, but on the
    zero-iteration path the pre-loop value was still owned at scope end
    and consumed by nothing — the backedge/exit merge had no dead-after
    handling, so drop_list showed no drop for ``v2`` (section 18
    exactly-once violated). Now the loop-skipped edge gets a branch-end
    drop anchored at the loop."""
    # Arrange
    source = (
        "fn consume(v: Vec<Int>) { push(v, 1)\n print(0) }\n"
        "fn f(c: Bool, v2: Vec<Int>) { while c { v2 = vec()\n consume(v2) } }"
    )
    # Act
    res = analyze(source)
    # Assert
    assert diag_codes(res) == []
    assert drop_list(res) == [
        ("consume", "<temp>", "after-stmt"),
        ("f", "v2", "branch-end"),
    ]


def test_last_use_then_return_still_gets_an_after_stmt_drop():
    """Regression: the after-stmt drop for a var whose final use had
    completed was only synthesized by the end-of-block pass, which runs
    only when some path falls through — with a trailing ``return`` (or
    every match arm returning) the before-return unwind excluded the
    anchored var AND the end-of-block pass never ran, so the value got
    NO DropPoint on any path (section 18 exactly-once violated)."""
    # Arrange / Act
    trailing = analyze("fn f() { let v = push(vec(), 1)\n print(len(v))\n return }")
    all_arms = analyze(
        "fn f(o: Option<Int>) { let v = push(vec(), 1)\n print(len(v))\n"
        " match o { Some(x) => { return }, None => { return } } }"
    )
    # Assert
    assert diag_codes(trailing) == []
    assert drop_list(trailing) == [("f", "v", "after-stmt")]
    assert diag_codes(all_arms) == []
    assert drop_list(all_arms) == [("f", "v", "after-stmt")]
    # Control: a return BEFORE the last use keeps both drop kinds.
    control = analyze(
        "fn f(c: Bool) { let v = push(vec(), 1)\n if c { return }\n print(len(v)) }"
    )
    assert diag_codes(control) == []
    assert drop_list(control) == [
        ("f", "v", "after-stmt"),
        ("f", "v", "before-return"),
    ]


def test_ox0403_note_points_at_the_later_use_not_just_the_move():
    """OX0403 must name BOTH ends of the conflict.

    In a loop the move site and the conflicting use are the same syntax,
    so OX0403's span and its move note pointed at the same place and the
    diagnostic never mentioned the use AFTER the loop -- the thing that
    makes the move fatal rather than merely repeated. Poisoning suppressed
    that use to keep diagnostics quiet, and suppressed the information
    with it.

    Empirically this misled real models: given the old diagnostic, a
    frontier subject cloned inside the loop (which OX0403's own
    suggestion recommended) in both Oxide dialects, producing a program
    that compiles, silences the error, and never accumulates. rustc names
    both ends and the same subject repaired the Rust version correctly.
    See eval/results/ownership-probe-frontier/REPORT.md.
    """
    src = (
        "fn main() {\n"
        "    let acc = range(0, 10)\n"
        "    print(len(acc))\n"
        "    for i in range(0, 3) {\n"
        "        let grown = push(acc, i)\n"
        "    }\n"
        "    print(len(acc))\n"
        "}\n"
    )
    diags = diags_with_code(analyze(src), "OX0403")
    assert len(diags) == 1, diags
    diag = diags[0]
    spans = {diag.span} | {span for _text, span in diag.notes}
    assert len(spans) >= 2, (
        "OX0403 still points at only one location; the later use that makes "
        "the move fatal is not named"
    )
    assert any(text == "later used here" for text, _ in diag.notes), diag.notes


def test_later_use_note_is_recorded_once_not_per_use():
    """Poisoning exists to stop one move cascading into a diagnostic at
    every later use. Recording the later use must not undo that."""
    src = (
        "fn main() {\n"
        "    let acc = range(0, 10)\n"
        "    for i in range(0, 3) {\n"
        "        let grown = push(acc, i)\n"
        "    }\n"
        "    print(len(acc))\n"
        "    print(len(acc))\n"
        "    print(len(acc))\n"
        "}\n"
    )
    diags = diags_with_code(analyze(src), "OX0403")
    assert len(diags) == 1, diags
    later = [t for t, _ in diags[0].notes if t == "later used here"]
    assert len(later) == 1, f"expected exactly one later-use note, got {len(later)}"
