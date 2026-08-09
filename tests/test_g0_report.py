"""Tests for the G0 profiler (eval/g0_report.py).

The discipline this module enforces on itself: it must reproduce the
published 6a-pilot numbers (eval/results/6a-pilot/REPORT.md) before it is
trusted to read new G0 data. ``test_profiler_reproduces_the_pilot_7b_row``
is that self-check, made a first-class test rather than a one-off manual
run.

These tests build synthetic run dirs under ``tmp_path`` only -- they must
never read ``eval/results/g0-generation-baseline/``, which live G0 runs
are writing to concurrently. ``eval/results/6a-pilot`` is the one
real/on-disk fixture, and it is finished, published data.
"""

import json
from pathlib import Path

import pytest

from eval import g0_report
from eval.driver import build_run_id


def test_profiler_reproduces_the_pilot_7b_row():
    # Published in eval/results/6a-pilot/REPORT.md: 7B 0-shot first-compile
    # oxide 2/20, explicit 0/20, rust 20/20; rust final-pass 12/20.
    out = g0_report.profile(
        root=Path("eval/results/6a-pilot"),
        models=["qwen7b"], seeds=[1], prefix="6a",
    )
    row = out["qwen7b"]
    assert row["oxide"]["first_compiled"] == 2 / 20
    assert row["explicit"]["first_compiled"] == 0 / 20
    assert row["rust"]["first_compiled"] == 20 / 20
    assert row["rust"]["final_passed"] == 12 / 20


def _write_run(root, slug, seed, cells, triples, prefix="g0c"):
    run_dir = root / build_run_id(slug, 0, seed, prefix=prefix)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cells.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in cells), encoding="utf-8"
    )
    (run_dir / "triples.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in triples), encoding="utf-8"
    )
    return run_dir


def _minimal_cell(task, arm, passed=False):
    """Only the fields profile()'s by_arm block and rollup.paired_delta
    actually read -- first_compiled/first_passed/final_passed plus task
    and arm."""
    return {
        "task": task, "arm": arm,
        "first_compiled": passed, "first_passed": passed,
        "final_passed": passed,
    }


def _complete_cells():
    """60 cells (20 per arm) -- satisfies eval.driver.is_complete's
    pinned SESSIONS_PER_RUN floor. is_complete only counts lines in
    cells.jsonl, so task/arm content is otherwise irrelevant to it; this
    fixture exists purely so tests that aren't exercising the
    completeness guard itself don't trip over it."""
    return [
        _minimal_cell(f"t{i:02d}", arm)
        for i in range(1, 21)
        for arm in ("oxide", "explicit", "rust")
    ]


def test_profiler_buckets_first_attempt_codes_into_all_five_stages(tmp_path):
    # One positive case per stage bucket -- OX0001 (lexer), OX01xx
    # (parser), OX02xx (resolve), OX03xx (types), OX04xx (linearity) --
    # so a bucket boundary regression anywhere in _stage can't hide
    # behind a fixture that only ever exercises two of the five.
    triples = [
        {"task": "t01", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0001"}]},
        {"task": "t02", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0100"}]},
        {"task": "t03", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0203"}]},
        {"task": "t04", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0300"}]},
        {"task": "t05", "arm": "explicit", "attempt": 1,
         "diagnostics": [{"code": "OX0400"}]},
        # rust diagnostics must never land in the stage histogram.
        {"task": "t01", "arm": "rust", "attempt": 1,
         "diagnostics": [{"code": "OX0001"}]},
    ]
    _write_run(tmp_path, "qwen7b", 1, _complete_cells(), triples)

    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
    )
    assert out["qwen7b"]["stage_hist_first"] == {
        "lexer": 1, "parser": 1, "resolve": 1, "types": 1, "linearity": 1,
    }


def test_profiler_all_attempts_histogram_counts_attempt_2_first_does_not(tmp_path):
    # stage_hist_first is first-attempt only; stage_hist_all is every
    # attempt. A diagnostic that appears ONLY on attempt 2 must move the
    # "all" count without moving the "first" count for that stage --
    # the one behavior nothing in the pilot-reproduction test or the
    # five-bucket test above actually distinguishes.
    triples = [
        {"task": "t01", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0001"}]},
        {"task": "t01", "arm": "oxide", "attempt": 2,
         "diagnostics": [{"code": "OX0001"}]},
    ]
    _write_run(tmp_path, "qwen7b", 1, _complete_cells(), triples)

    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
    )
    assert out["qwen7b"]["stage_hist_first"]["lexer"] == 1
    assert out["qwen7b"]["stage_hist_all"]["lexer"] == 2


def test_profiler_counts_gate_occurrences_and_sessions_separately(tmp_path):
    # One session (t01/oxide) carries TWO OX04xx diagnostics ALONGSIDE a
    # non-OX04xx code (OX0100, parser); the non-OX04xx code must be
    # excluded from the gate count -- a regression that counts every
    # diagnostic on the attempt (`ox04 = len(codes)`) would inflate this
    # session's contribution from 2 to 3 and still pass a fixture where
    # every diagnostic happens to be OX04xx. A second session (t02/oxide)
    # carries ONE. Occurrences must sum the raw OX04xx-only diagnostic
    # count (3); sessions must count distinct sessions (2).
    triples = [
        {"task": "t01", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0400"}, {"code": "OX0401"},
                         {"code": "OX0100"}]},
        {"task": "t02", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0402"}]},
        # a second (repair) attempt on the same session must not double
        # count -- the gate metric is pooled FIRST-attempt only.
        {"task": "t01", "arm": "oxide", "attempt": 2,
         "diagnostics": [{"code": "OX0400"}]},
    ]
    _write_run(tmp_path, "qwen7b", 1, _complete_cells(), triples)

    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
    )
    gate = out["qwen7b"]["gate"]
    assert gate["occurrences"] == 3
    assert gate["sessions"] == 2


# ------------------------------------------------- context_exhausted


def test_profiler_reports_context_exhausted_counts_and_by_arm_split(tmp_path):
    # Exhausted cells in TWO arms (oxide, explicit) -- the asymmetry the
    # report has to disclose -- plus a clean rust arm. "cells" must be
    # the sum across arms and "by_arm" must attribute each cell to its
    # own arm, not pool them.
    cells = _complete_cells()
    exhausted_keys = {("t01", "oxide"), ("t02", "oxide"), ("t01", "explicit")}
    for cell in cells:
        if (cell["task"], cell["arm"]) in exhausted_keys:
            cell["context_exhausted"] = True
    _write_run(tmp_path, "qwen7b", 1, cells, [])

    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
    )
    exhausted = out["qwen7b"]["context_exhausted"]
    assert exhausted["cells"] == 3
    assert exhausted["by_arm"] == {"oxide": 2, "explicit": 1, "rust": 0}


def test_profiler_context_exhausted_is_zero_when_field_is_absent(tmp_path):
    # None of _complete_cells()'s records carry "context_exhausted" at
    # all (the field is omitted entirely, never written as False) --
    # the common case for every session that never overflowed.
    _write_run(tmp_path, "qwen7b", 1, _complete_cells(), [])
    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
    )
    assert out["qwen7b"]["context_exhausted"] == {
        "cells": 0,
        "by_arm": {"oxide": 0, "explicit": 0, "rust": 0},
    }


def test_print_report_includes_the_context_exhausted_line(tmp_path, capsys):
    cells = _complete_cells()
    for cell in cells:
        if cell["task"] == "t01" and cell["arm"] == "oxide":
            cell["context_exhausted"] = True
    _write_run(tmp_path, "qwen7b", 1, cells, [])

    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
    )
    g0_report._print_report(out, ["qwen7b"])
    printed = capsys.readouterr().out
    assert "context_exhausted: 1 cell(s)" in printed
    assert "oxide 1, explicit 0, rust 0" in printed


# ------------------------------------------- incomplete/partial roots


def test_profile_raises_a_clear_error_naming_a_missing_run(tmp_path):
    # No run dir at all under tmp_path for qwen7b/seed 1 -- the
    # foreseeable case of pointing --root at an in-progress or
    # not-yet-started G0 root.
    with pytest.raises(RuntimeError) as exc_info:
        g0_report.profile(
            root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
        )
    message = str(exc_info.value)
    assert build_run_id("qwen7b", 0, 1, prefix="g0c") in message
    assert "--partial" in message


def test_profile_raises_for_a_run_short_of_the_pinned_session_count(tmp_path):
    # A run dir exists but cells.jsonl has fewer than the pinned 60
    # sessions -- still in progress, distinct from "missing entirely".
    _write_run(tmp_path, "qwen7b", 1, _complete_cells()[:5], [])
    with pytest.raises(RuntimeError, match="incomplete"):
        g0_report.profile(
            root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
        )


def test_profile_partial_skips_incomplete_runs_and_profiles_the_rest(tmp_path):
    # seed 1 is complete; seed 2 is entirely missing. Without --partial
    # this raises (covered above); with it, profiling proceeds using
    # only seed 1's data.
    _write_run(tmp_path, "qwen7b", 1, _complete_cells(), [])
    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1, 2], prefix="g0c",
        partial=True,
    )
    assert out["qwen7b"]["oxide"]["n"] == 20  # seed 1 only, not doubled


def test_profile_raises_a_clear_error_when_an_arm_has_zero_cells(tmp_path):
    # 60 total cells (satisfies is_complete's line-count floor) but ZERO
    # of them are the rust arm -- the ZeroDivisionError this guard
    # replaces with a named, actionable error.
    cells = (
        [_minimal_cell(f"t{i:02d}", "oxide") for i in range(1, 31)]
        + [_minimal_cell(f"t{i:02d}", "explicit") for i in range(1, 31)]
    )
    _write_run(tmp_path, "qwen7b", 1, cells, [])
    with pytest.raises(RuntimeError, match="rust"):
        g0_report.profile(
            root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
        )


# --------------------------------------------------- --samples output


def test_write_samples_does_not_collide_across_seeds_for_the_same_task_arm(
    tmp_path,
):
    # The same (task, arm) pair recurs across every seed of a model.
    # Each seed's sample must land as its own file, attributable to its
    # run id, not overwrite the other.
    for seed in (1, 2):
        triples = [
            {"task": "t16", "arm": "explicit", "attempt": 1,
             "diagnostics": [{"code": "OX0203"}], "compiled": False,
             "passed": False},
        ]
        run_dir = _write_run(tmp_path, "qwen7b", seed, _complete_cells(),
                             triples)
        raw_dir = run_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "t16.explicit.1.txt").write_text(f"seed {seed}\n",
                                                      encoding="utf-8")

    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1, 2], prefix="g0c"
    )
    copied = g0_report.write_samples(
        root=tmp_path, models=["qwen7b"], seeds=[1, 2], prefix="g0c",
        profiled=out, n=5,
    )
    assert copied == 2
    dest_dir = tmp_path / "samples" / "resolve" / "OX0203"
    seed1 = dest_dir / f"{build_run_id('qwen7b', 0, 1, prefix='g0c')}.t16.explicit.txt"
    seed2 = dest_dir / f"{build_run_id('qwen7b', 0, 2, prefix='g0c')}.t16.explicit.txt"
    assert seed1.read_text(encoding="utf-8") == "seed 1\n"
    assert seed2.read_text(encoding="utf-8") == "seed 2\n"


# ------------------------------------------------ --demand-histogram


def test_demand_histogram_counts_pinned_chars_from_first_attempt_raw_text(
    tmp_path,
):
    # The taxonomy's pinned definition (docs/superpowers/specs/
    # 2026-08-09-v03-taxonomy.md, "The demand histogram, and a
    # validation finding"): raw character occurrences over ";[]'|&#",
    # first attempts, oxide+explicit pooled, per family.
    run_dir = _write_run(tmp_path, "qwen7b", 1, _complete_cells(), [])
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "t01.oxide.1.txt").write_text("a; b[0] | c\n", encoding="utf-8")
    (raw_dir / "t02.explicit.1.txt").write_text("x & y; z#\n", encoding="utf-8")
    # A second (repair) attempt and a rust-arm first attempt must NOT be
    # counted -- pinned to first attempts, oxide+explicit only.
    (raw_dir / "t01.oxide.2.txt").write_text(";;;;;\n", encoding="utf-8")
    (raw_dir / "t03.rust.1.txt").write_text(";;;;;\n", encoding="utf-8")

    histogram = g0_report.demand_histogram(
        root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c",
    )
    assert histogram["qwen7b"] == {
        ";": 2, "[": 1, "]": 1, "'": 0, "|": 1, "&": 1, "#": 1,
    }
